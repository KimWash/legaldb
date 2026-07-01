from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import shutil
import statistics
import subprocess
import time
from typing import Iterator


@dataclass
class AdvancedPageResult:
    page_index: int
    text_length: int
    mean_confidence: float
    used_psm: int
    used_crop: bool
    status: str
    text_file: str


@dataclass
class AdvancedOcrResult:
    text: str
    mean_confidence: float
    failed_pages: list[int]
    total_pages: int
    run_dir: Path
    status: str


def _ensure_dirs(base: Path) -> dict[str, Path]:
    rendered = base / "rendered"
    processed = base / "processed"
    page_txt = base / "page_txt"
    for directory in [base, rendered, processed, page_txt]:
        directory.mkdir(parents=True, exist_ok=True)
    return {"base": base, "rendered": rendered, "processed": processed, "page_txt": page_txt}


def _get_pdf_page_count(pdf_path: Path) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(doc)
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _iter_pdf_pages(pdf_path: Path, dpi: int, max_dim_limit: int = 0) -> Iterator[tuple[int, object]]:
    """Lazily yield (page_no, bgr_image) one page at a time to avoid loading all pages into memory.

    doc.close() is guaranteed via try/finally even when the caller breaks early or
    an exception is raised, preventing "IO Operation on closed file" in multi-threaded use.
    Per-page objects are explicitly deleted to release memory promptly.
    """
    import pypdfium2 as pdfium
    import cv2
    import numpy as np

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            
            # Adaptive scale calculation: render at target max dimension directly
            if max_dim_limit > 0:
                orig_w, orig_h = page.get_size()
                orig_max = max(orig_w, orig_h)
                default_pixel_max = orig_max * (dpi / 72.0)
                if default_pixel_max > max_dim_limit:
                    scale = max_dim_limit / orig_max
                else:
                    scale = dpi / 72.0
            else:
                scale = dpi / 72.0
                
            render_pil = page.render(scale=scale).to_pil()
            pil_img = render_pil.convert("RGB")
            rgb = np.array(pil_img)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            yield (page_index + 1, bgr)
            
            # Explicitly close PIL images to release backend C-buffers immediately
            try:
                render_pil.close()
                pil_img.close()
            except Exception:
                pass
                
            # Explicitly release per-page objects to avoid memory accumulation
            del page, render_pil, pil_img, rgb, bgr
    finally:
        # Guaranteed cleanup even on break, timeout, or exception
        try:
            doc.close()
        except Exception:
            pass


def _iter_pdf_pages_queued(
    pdf_path: Path,
    dpi: int,
    max_dim_limit: int = 0,
    queue_size: int = 4
) -> Iterator[tuple[int, object]]:
    """Runs PDF rendering in a background thread using a Queue to overlap with OCR inference."""
    import queue
    import threading

    q = queue.Queue(maxsize=queue_size)

    def producer():
        try:
            for page_no, image in _iter_pdf_pages(pdf_path, dpi=dpi, max_dim_limit=max_dim_limit):
                q.put((page_no, image))
            q.put((None, None))  # Sentinel for EOF
        except Exception as exc:
            q.put((None, exc))

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    while True:
        page_no, item = q.get()
        if page_no is None:
            if isinstance(item, Exception):
                raise item
            break
        yield page_no, item


def _preprocess_image(image):
    import cv2
    import numpy as np

    # Minimize image copies by working in-place as much as possible
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    del image  # Release original image reference immediately

    cv2.convertScaleAbs(gray, dst=gray, alpha=1.6, beta=8)
    cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        12,
        dst=gray
    )
    
    denoised = cv2.fastNlMeansDenoising(gray, h=15, templateWindowSize=7, searchWindowSize=21)
    del gray  # Release gray buffer immediately

    sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    cv2.filter2D(denoised, -1, sharpen_kernel, dst=denoised)
    
    padded = cv2.copyMakeBorder(denoised, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    del denoised  # Release denoised buffer immediately
    
    return padded


def _auto_crop_body_region(image):
    import cv2

    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    binary_inv = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    points = cv2.findNonZero(binary_inv)
    if points is None:
        return image
    x, y, w, h = cv2.boundingRect(points)
    margin_x = max(16, int(w * 0.03))
    margin_y = max(16, int(h * 0.03))
    x1 = max(0, x - margin_x)
    y1 = max(0, y - margin_y)
    x2 = min(gray.shape[1], x + w + margin_x)
    y2 = min(gray.shape[0], y + h + margin_y)
    cropped = image[y1:y2, x1:x2]
    return cropped if cropped.size else image


def _ocr_image(image, lang: str, psm: int):
    import pytesseract

    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT)
    tokens: list[str] = []
    conf_values: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        t = (text or "").strip()
        if t:
            tokens.append(t)
        try:
            c = float(conf)
            if c >= 0:
                conf_values.append(c)
        except Exception:
            pass
    merged = " ".join(tokens).strip()
    mean_conf = statistics.mean(conf_values) if conf_values else 0.0
    return merged, mean_conf


def _choose_best_text(preprocessed, min_len: int, lang: str):
    text6, conf6 = _ocr_image(preprocessed, lang=lang, psm=6)
    if len(text6) >= min_len:
        return text6, conf6, 6, False, preprocessed

    text11, conf11 = _ocr_image(preprocessed, lang=lang, psm=11)
    if len(text11) >= min_len:
        return text11, conf11, 11, False, preprocessed

    cropped = _auto_crop_body_region(preprocessed)
    crop6, cconf6 = _ocr_image(cropped, lang=lang, psm=6)
    if len(crop6) >= min_len:
        return crop6, cconf6, 6, True, cropped

    crop11, cconf11 = _ocr_image(cropped, lang=lang, psm=11)
    candidates = [
        (text6, conf6, 6, False, preprocessed),
        (text11, conf11, 11, False, preprocessed),
        (crop6, cconf6, 6, True, cropped),
        (crop11, cconf11, 11, True, cropped),
    ]
    best = max(candidates, key=lambda item: (len(item[0]), item[1]))
    del candidates  # Release references in the candidates list
    return best


def _resolve_tesseract_cmd(config: dict) -> str:
    custom = str(config.get("tesseract_cmd", "") or "").strip()
    if custom:
        return custom
    return "tesseract"


def run_advanced_ocr(pdf_path: Path, temp_dir: Path, config: dict, timeout_seconds: int = 0) -> AdvancedOcrResult:
    import cv2
    import pytesseract

    run_key = hashlib.sha1(str(pdf_path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    run_dir = temp_dir / "ocr_runs" / f"run_{run_key}"
    dirs = _ensure_dirs(run_dir)

    tesseract_cmd = _resolve_tesseract_cmd(config)
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        subprocess.run([tesseract_cmd, "--version"], check=True, capture_output=True, text=True)
    except Exception as exc:
        return AdvancedOcrResult(
            text="",
            mean_confidence=0.0,
            failed_pages=[],
            total_pages=0,
            run_dir=run_dir,
            status=f"tesseract_not_available:{exc}",
        )

    dpi = int(config.get("dpi", 400))
    lang = str(config.get("language", "eng"))
    min_len = int(config.get("min_len", 40))
    save_debug_images = bool(config.get("save_debug_images", False))

    total_pages = _get_pdf_page_count(pdf_path)
    page_results: list[AdvancedPageResult] = []
    failed_pages: list[int] = []
    merged_parts: list[str] = []
    all_conf: list[float] = []
    timed_out = False
    start_time = time.monotonic()

    for page_no, image in _iter_pdf_pages_queued(pdf_path, dpi=dpi):
        if timeout_seconds > 0 and time.monotonic() - start_time > timeout_seconds:
            timed_out = True
            break
        preprocessed = _preprocess_image(image)
        if save_debug_images:
            cv2.imwrite(str(dirs["rendered"] / f"page_{page_no:04d}.png"), image)
            cv2.imwrite(str(dirs["processed"] / f"page_{page_no:04d}_preprocessed.png"), preprocessed)
        text, conf, psm, used_crop, used_img = _choose_best_text(preprocessed, min_len=min_len, lang=lang)
        if used_crop and save_debug_images:
            cv2.imwrite(str(dirs["processed"] / f"page_{page_no:04d}_cropped.png"), used_img)

        normalized = text.strip() or "__NO_TEXT__"
        text_len = 0 if normalized == "__NO_TEXT__" else len(normalized)
        txt_path = dirs["page_txt"] / f"page_{page_no:04d}.txt"
        txt_path.write_text(normalized, encoding="utf-8")

        if normalized == "__NO_TEXT__" or text_len < min_len:
            status = "failed"
            failed_pages.append(page_no)
        else:
            status = "success"
            merged_parts.append(f"===== PAGE {page_no} =====\n{normalized}")

        all_conf.append(conf)
        page_results.append(
            AdvancedPageResult(
                page_index=page_no,
                text_length=text_len,
                mean_confidence=round(conf, 3),
                used_psm=psm,
                used_crop=used_crop,
                status=status,
                text_file=str(txt_path),
            )
        )
        
        # Release local references immediately at end of loop to allow early garbage collection
        try:
            del image, preprocessed, used_img
        except Exception:
            pass

    merged_text = "\n\n".join(merged_parts).strip() if merged_parts else "__NO_TEXT__"
    merged_path = dirs["base"] / "merged.txt"
    merged_path.write_text(merged_text, encoding="utf-8")

    processed_pages = len(page_results)
    mean_conf = round(statistics.mean(all_conf), 3) if all_conf else 0.0
    if timed_out:
        elapsed = round(time.monotonic() - start_time, 1)
        final_status = "timeout_partial" if merged_text != "__NO_TEXT__" else "timeout_no_text"
    else:
        final_status = "success" if merged_text != "__NO_TEXT__" else "no_text"

    # ── 임시 파일 정리 (대용량 rendered/processed PNG 삭제) ────────────────────────
    # save_debug_images=True 이면 보존, False(기본) + cleanup_after_ocr=True 이면 삭제
    cleanup_enabled = bool(config.get("cleanup_after_ocr", True))
    if cleanup_enabled and not save_debug_images:
        for _subdir in ("rendered", "processed", "page_txt"):
            try:
                shutil.rmtree(dirs[_subdir], ignore_errors=True)
            except Exception:
                pass
        print(f"[ocr_advanced] 임시 이미지 정리 완료: {run_dir}")

    summary = {
        "pdf_path": str(pdf_path),
        "total_pages": total_pages,
        "processed_pages": processed_pages,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "success_pages": sum(1 for item in page_results if item.status == "success"),
        "failed_pages": failed_pages,
        "mean_confidence": mean_conf,
        "settings": {
            "dpi": dpi,
            "lang": lang,
            "oem": 1,
            "primary_psm": 6,
            "fallback_psm": 11,
            "min_len": min_len,
        },
        "pages": [asdict(item) for item in page_results],
        "merged_text_file": str(merged_path),
    }
    summary_path = dirs["base"] / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return AdvancedOcrResult(
        text=merged_text if merged_text != "__NO_TEXT__" else "",
        mean_confidence=mean_conf,
        failed_pages=failed_pages,
        total_pages=total_pages,
        run_dir=run_dir,
        status=final_status,
    )
