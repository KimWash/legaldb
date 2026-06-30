from __future__ import annotations

"""Intel-accelerated OCR backend (OpenVINO + RapidOCR).

Targets the Intel Ultra 7 155H: tries Arc GPU first, then NPU, then CPU.
Falls back gracefully (status='unavailable') when the optional dependency or a
hardware device is missing, so callers can drop back to the Tesseract pipeline.

Public API mirrors ocr_advanced so extractors can swap backends:
  - run_accelerated_ocr(pdf_path, temp_dir, config, timeout_seconds) -> AdvancedOcrResult
  - ocr_image_array(image, config) -> (text, mean_confidence_0_100)
"""

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import statistics
import threading
import time

from ocr_advanced import (
    AdvancedOcrResult,
    AdvancedPageResult,
    _ensure_dirs,
    _get_pdf_page_count,
    _iter_pdf_pages,
    _preprocess_image,
)

# 엔진은 프로세스당 1회만 생성 (모델 로드 비용이 크므로 캐시)
_ENGINE_LOCK = threading.Lock()
_ENGINE_STATE: dict | None = None  # {"engine", "device", "backend"} or {"engine": None, ...}


def _device_priority(config: dict) -> list[str]:
    raw = config.get("device_priority") or ["GPU", "NPU", "CPU"]
    out: list[str] = []
    for d in raw:
        d = str(d).strip().upper()
        if d and d not in out:
            out.append(d)
    return out or ["CPU"]


def _try_build_rapidocr_openvino(device_priority: list[str]):
    """Older `rapidocr_openvino` package. Device is best-effort via env hint."""
    import os
    try:
        from rapidocr_openvino import RapidOCR
    except Exception:
        return None
    for device in device_priority:
        try:
            # Some builds honor this env hint for the OpenVINO inference device.
            os.environ["RAPIDOCR_OPENVINO_INFERENCE_DEVICE"] = device
            engine = RapidOCR()
            return engine, device, "rapidocr_openvino"
        except Exception:
            continue
    return None


def _try_build_rapidocr(device_priority: list[str], lang: str):
    """Newer unified `rapidocr` (>=2.0) with explicit OpenVINO engine + device."""
    try:
        from rapidocr import RapidOCR, EngineType  # type: ignore
    except Exception:
        return None
    for device in device_priority:
        try:
            params = {
                "Det.engine_type": EngineType.OPENVINO,
                "Cls.engine_type": EngineType.OPENVINO,
                "Rec.engine_type": EngineType.OPENVINO,
                "EngineConfig.openvino.inference_device": device,
            }
            engine = RapidOCR(params=params)
            return engine, device, "rapidocr"
        except Exception:
            continue
    return None


def _get_engine(config: dict):
    """Return (engine, device, backend) or (None, '', 'unavailable'); built once."""
    global _ENGINE_STATE
    if _ENGINE_STATE is not None:
        st = _ENGINE_STATE
        return st["engine"], st["device"], st["backend"]
    with _ENGINE_LOCK:
        if _ENGINE_STATE is not None:
            st = _ENGINE_STATE
            return st["engine"], st["device"], st["backend"]
        device_priority = _device_priority(config)
        lang = str(config.get("lang", "korean"))
        built = _try_build_rapidocr(device_priority, lang) or _try_build_rapidocr_openvino(device_priority)
        if built is None:
            print("[ocr_accelerated] RapidOCR/OpenVINO unavailable — falling back to Tesseract pipeline.")
            _ENGINE_STATE = {"engine": None, "device": "", "backend": "unavailable"}
        else:
            engine, device, backend = built
            print(f"[ocr_accelerated] engine ready backend={backend} device={device}")
            _ENGINE_STATE = {"engine": engine, "device": device, "backend": backend}
        st = _ENGINE_STATE
        return st["engine"], st["device"], st["backend"]


def is_available(config: dict) -> bool:
    engine, _device, backend = _get_engine(config)
    return engine is not None and backend != "unavailable"


def _run_engine_on_image(engine, image) -> tuple[str, float]:
    """Run RapidOCR on a BGR/RGB numpy image. Returns (text, mean_conf_0_100)."""
    output = engine(image)
    # rapidocr_openvino: returns (result, elapse); result = [[box, text, score], ...] or None
    # rapidocr 2.x: returns an object with .txts / .scores
    texts: list[str] = []
    scores: list[float] = []
    if isinstance(output, tuple):
        result = output[0]
        if result:
            for line in result:
                try:
                    texts.append(str(line[1]))
                    scores.append(float(line[2]))
                except Exception:
                    continue
    else:
        # object-style result (rapidocr 2.x)
        txts = getattr(output, "txts", None)
        scs = getattr(output, "scores", None)
        if txts:
            for i, t in enumerate(txts):
                texts.append(str(t))
                try:
                    scores.append(float(scs[i]))
                except Exception:
                    pass
    merged = " ".join(t.strip() for t in texts if t and t.strip()).strip()
    mean_conf = (statistics.mean(scores) * 100.0) if scores else 0.0
    return merged, mean_conf


def ocr_image_array(image, config: dict) -> tuple[str, float] | None:
    """OCR a single preprocessed image array. Returns (text, conf_0_100) or None if backend unavailable."""
    engine, _device, backend = _get_engine(config)
    if engine is None or backend == "unavailable":
        return None
    try:
        return _run_engine_on_image(engine, image)
    except Exception as exc:
        print(f"[ocr_accelerated] image OCR failed ({exc}); will fall back.")
        return None


def run_accelerated_ocr(pdf_path: Path, temp_dir: Path, config: dict, timeout_seconds: int = 0) -> AdvancedOcrResult:
    """OCR a PDF page-by-page with the OpenVINO/RapidOCR backend.

    Returns AdvancedOcrResult (status='unavailable' if the backend cannot be used,
    so the caller can fall back to Tesseract).
    """
    run_key = hashlib.sha1(str(pdf_path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    run_dir = temp_dir / "ocr_runs" / f"accel_{run_key}"

    engine, device, backend = _get_engine(config)
    if engine is None or backend == "unavailable":
        return AdvancedOcrResult(
            text="", mean_confidence=0.0, failed_pages=[], total_pages=0,
            run_dir=run_dir, status="unavailable",
        )

    dirs = _ensure_dirs(run_dir)
    dpi = int(config.get("dpi", 300))
    min_len = int(config.get("min_len", 40))
    preprocess = bool(config.get("preprocess", False))

    try:
        total_pages = _get_pdf_page_count(pdf_path)
    except Exception as exc:
        return AdvancedOcrResult(
            text="", mean_confidence=0.0, failed_pages=[], total_pages=0,
            run_dir=run_dir, status=f"render_error:{exc}",
        )

    page_results: list[AdvancedPageResult] = []
    failed_pages: list[int] = []
    merged_parts: list[str] = []
    all_conf: list[float] = []
    timed_out = False
    start_time = time.monotonic()

    for page_no, image in _iter_pdf_pages(pdf_path, dpi=dpi):
        if timeout_seconds > 0 and time.monotonic() - start_time > timeout_seconds:
            timed_out = True
            break
        img = _preprocess_image(image) if preprocess else image
        try:
            text, conf = _run_engine_on_image(engine, img)
        except Exception as exc:
            text, conf = "", 0.0
            print(f"[ocr_accelerated] page {page_no} OCR failed: {exc}")

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
                page_index=page_no, text_length=text_len,
                mean_confidence=round(conf, 3), used_psm=0, used_crop=False,
                status=status, text_file=str(txt_path),
            )
        )

    merged_text = "\n\n".join(merged_parts).strip() if merged_parts else "__NO_TEXT__"
    (dirs["base"] / "merged.txt").write_text(merged_text, encoding="utf-8")
    mean_conf = round(statistics.mean(all_conf), 3) if all_conf else 0.0
    if timed_out:
        final_status = "timeout_partial" if merged_text != "__NO_TEXT__" else "timeout_no_text"
    else:
        final_status = "success" if merged_text != "__NO_TEXT__" else "no_text"

    summary = {
        "pdf_path": str(pdf_path),
        "backend": backend,
        "device": device,
        "total_pages": total_pages,
        "processed_pages": len(page_results),
        "timed_out": timed_out,
        "success_pages": sum(1 for it in page_results if it.status == "success"),
        "failed_pages": failed_pages,
        "mean_confidence": mean_conf,
        "settings": {"dpi": dpi, "min_len": min_len, "preprocess": preprocess},
        "pages": [asdict(it) for it in page_results],
    }
    (dirs["base"] / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return AdvancedOcrResult(
        text=merged_text if merged_text != "__NO_TEXT__" else "",
        mean_confidence=mean_conf,
        failed_pages=failed_pages,
        total_pages=total_pages,
        run_dir=run_dir,
        status=final_status,
    )
