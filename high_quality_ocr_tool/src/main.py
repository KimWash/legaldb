from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import argparse
import json
import os
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

@dataclass
class PageResult:
    page_index: int
    text_length: int
    mean_confidence: float
    used_psm: int
    used_crop: bool
    status: str
    text_file: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Low-quality PDF OCR pipeline")
    parser.add_argument("--pdf", required=True, help="Input PDF path")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--dpi", type=int, default=400, help="Render DPI")
    parser.add_argument("--lang", default="eng", help="Tesseract language")
    parser.add_argument("--min-len", type=int, default=40, help="Minimum accepted text length per page")
    parser.add_argument("--tesseract-cmd", default="", help="Optional absolute path to tesseract executable")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="Parallel OCR workers")
    parser.add_argument("--save-debug-images", action="store_true", help="Save rendered/preprocessed/cropped images")
    parser.add_argument("--crop-max-pre-conf", type=float, default=60.0, help="Crop retry only if pre-crop confidence is below this value")
    parser.add_argument("--crop-min-margin-ratio", type=float, default=0.10, help="Crop retry only if estimated margin ratio is above this value")
    return parser.parse_args()


def ensure_dirs(base: Path) -> dict[str, Path]:
    rendered = base / "rendered"
    processed = base / "processed"
    page_txt = base / "page_txt"
    for directory in [base, rendered, processed, page_txt]:
        directory.mkdir(parents=True, exist_ok=True)
    return {"base": base, "rendered": rendered, "processed": processed, "page_txt": page_txt}


def render_pdf_pages(pdf_path: Path, dpi: int) -> list:
    import pypdfium2 as pdfium
    import cv2
    import numpy as np

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(pdf_path))
    pages: list = []
    for index in range(len(doc)):
        page = doc[index]
        pil_img = page.render(scale=scale).to_pil().convert("RGB")
        rgb = np.array(pil_img)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        pages.append(bgr)
    return pages


def preprocess_image(image: np.ndarray) -> np.ndarray:
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    contrast = cv2.convertScaleAbs(gray, alpha=1.6, beta=8)
    thresholded = cv2.adaptiveThreshold(
        contrast,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        12,
    )
    denoised = cv2.fastNlMeansDenoising(thresholded, h=15, templateWindowSize=7, searchWindowSize=21)
    sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    sharpened = cv2.filter2D(denoised, -1, sharpen_kernel)
    padded = cv2.copyMakeBorder(sharpened, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    return padded


def auto_crop_body_region(image: np.ndarray) -> np.ndarray:
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
    if cropped.size == 0:
        return image
    return cropped


def estimate_margin_ratio(image: np.ndarray) -> float:
    import cv2

    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    binary_inv = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    points = cv2.findNonZero(binary_inv)
    if points is None:
        return 0.0
    x, y, w, h = cv2.boundingRect(points)
    page_area = float(gray.shape[0] * gray.shape[1])
    content_area = float(w * h) if w > 0 and h > 0 else page_area
    return max(0.0, min(1.0, 1.0 - (content_area / page_area)))


def run_tesseract_text(image: np.ndarray, lang: str, psm: int) -> str:
    import pytesseract

    config = f"--oem 1 --psm {psm}"
    text = pytesseract.image_to_string(image, lang=lang, config=config)
    return " ".join((text or "").split()).strip()


def run_tesseract_confidence(image: np.ndarray, lang: str, psm: int) -> float:
    import pytesseract

    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT)
    conf_values: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        value = (text or "").strip()
        if not value:
            continue
        try:
            conf_num = float(conf)
            if conf_num >= 0:
                conf_values.append(conf_num)
        except (ValueError, TypeError):
            pass
    return statistics.mean(conf_values) if conf_values else 0.0


def choose_best_result(
    preprocessed: np.ndarray,
    min_len: int,
    lang: str,
    crop_max_pre_conf: float,
    crop_min_margin_ratio: float,
) -> tuple[str, float, int, bool, np.ndarray]:
    # Fast pass: text-only OCR for candidate selection.
    text_6 = run_tesseract_text(preprocessed, lang=lang, psm=6)
    if len(text_6) >= min_len:
        conf_6 = run_tesseract_confidence(preprocessed, lang=lang, psm=6)
        return text_6, conf_6, 6, False, preprocessed

    text_11 = run_tesseract_text(preprocessed, lang=lang, psm=11)
    if len(text_11) >= min_len:
        conf_11 = run_tesseract_confidence(preprocessed, lang=lang, psm=11)
        return text_11, conf_11, 11, False, preprocessed

    # Crop retry only when short text + low confidence + large margins.
    best_text = text_6 if len(text_6) >= len(text_11) else text_11
    best_psm = 6 if len(text_6) >= len(text_11) else 11
    pre_conf = run_tesseract_confidence(preprocessed, lang=lang, psm=best_psm)
    margin_ratio = estimate_margin_ratio(preprocessed)
    should_crop = (
        len(best_text) < min_len
        and pre_conf < crop_max_pre_conf
        and margin_ratio >= crop_min_margin_ratio
    )
    if not should_crop:
        return best_text, pre_conf, best_psm, False, preprocessed

    cropped = auto_crop_body_region(preprocessed)
    crop_text_6 = run_tesseract_text(cropped, lang=lang, psm=6)
    if len(crop_text_6) >= min_len:
        crop_conf_6 = run_tesseract_confidence(cropped, lang=lang, psm=6)
        return crop_text_6, crop_conf_6, 6, True, cropped

    crop_text_11 = run_tesseract_text(cropped, lang=lang, psm=11)
    crop_conf_11 = run_tesseract_confidence(cropped, lang=lang, psm=11)
    crop_conf_6 = run_tesseract_confidence(cropped, lang=lang, psm=6)
    candidates = [
        (best_text, pre_conf, best_psm, False, preprocessed),
        (crop_text_6, crop_conf_6, 6, True, cropped),
        (crop_text_11, crop_conf_11, 11, True, cropped),
    ]
    best = max(candidates, key=lambda item: (len(item[0]), item[1]))
    return best


def verify_tesseract_binary(custom_cmd: str) -> None:
    import pytesseract

    if custom_cmd:
        pytesseract.pytesseract.tesseract_cmd = custom_cmd
    try:
        subprocess.run(
            [pytesseract.pytesseract.tesseract_cmd, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        raise SystemExit(f"Tesseract is not executable: {exc}") from exc


def process_page(
    idx: int,
    image: np.ndarray,
    dirs: dict[str, Path],
    args: argparse.Namespace,
) -> tuple[PageResult, str, bool]:
    import cv2

    preprocessed = preprocess_image(image)
    if args.save_debug_images:
        cv2.imwrite(str(dirs["rendered"] / f"page_{idx:04d}.png"), image)
        cv2.imwrite(str(dirs["processed"] / f"page_{idx:04d}_preprocessed.png"), preprocessed)

    text, conf, used_psm, used_crop, used_img = choose_best_result(
        preprocessed,
        min_len=args.min_len,
        lang=args.lang,
        crop_max_pre_conf=args.crop_max_pre_conf,
        crop_min_margin_ratio=args.crop_min_margin_ratio,
    )
    if used_crop and args.save_debug_images:
        cv2.imwrite(str(dirs["processed"] / f"page_{idx:04d}_cropped.png"), used_img)

    normalized = text.strip() or "__NO_TEXT__"
    page_txt = dirs["page_txt"] / f"page_{idx:04d}.txt"
    page_txt.write_text(normalized, encoding="utf-8")
    success = normalized != "__NO_TEXT__"
    merged_block = f"===== PAGE {idx} =====\n{normalized}" if success else ""
    result = PageResult(
        page_index=idx,
        text_length=0 if not success else len(normalized),
        mean_confidence=round(conf, 3),
        used_psm=used_psm,
        used_crop=used_crop,
        status="success" if success else "failed",
        text_file=str(page_txt),
    )
    return result, merged_block, success


def main() -> int:
    args = parse_args()
    import cv2

    pdf_path = Path(args.pdf).resolve()
    out_dir = Path(args.out).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    verify_tesseract_binary(args.tesseract_cmd)
    dirs = ensure_dirs(out_dir)
    page_images = render_pdf_pages(pdf_path, dpi=args.dpi)

    page_results: list[PageResult] = []
    merged_parts: list[tuple[int, str]] = []
    failed_pages: list[int] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(process_page, idx, image, dirs, args): idx
            for idx, image in enumerate(page_images, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                page_result, merged_block, success = future.result()
            except Exception:
                failed_pages.append(idx)
                page_txt = dirs["page_txt"] / f"page_{idx:04d}.txt"
                page_txt.write_text("__NO_TEXT__", encoding="utf-8")
                page_result = PageResult(idx, 0, 0.0, 6, False, "worker_failed", str(page_txt))
                merged_block, success = "", False
            page_results.append(page_result)
            if success and merged_block:
                merged_parts.append((idx, merged_block))
            if not success:
                failed_pages.append(idx)

    page_results.sort(key=lambda item: item.page_index)
    merged_parts.sort(key=lambda item: item[0])

    merged_text = "\n\n".join(block for _, block in merged_parts).strip() if merged_parts else "__NO_TEXT__"
    merged_path = dirs["base"] / "merged.txt"
    merged_path.write_text(merged_text, encoding="utf-8")

    summary = {
        "pdf_path": str(pdf_path),
        "output_dir": str(out_dir),
        "settings": {
            "dpi": args.dpi,
            "lang": args.lang,
            "oem": 1,
            "primary_psm": 6,
            "fallback_psm": 11,
            "min_len": args.min_len,
            "workers": args.workers,
            "save_debug_images": args.save_debug_images,
            "crop_max_pre_conf": args.crop_max_pre_conf,
            "crop_min_margin_ratio": args.crop_min_margin_ratio,
        },
        "total_pages": len(page_images),
        "success_pages": sum(1 for item in page_results if item.status == "success"),
        "failed_pages": failed_pages,
        "pages": [asdict(item) for item in page_results],
        "merged_text_file": str(merged_path),
    }
    summary_path = dirs["base"] / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"summary_file": str(summary_path), "merged_text_file": str(merged_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
