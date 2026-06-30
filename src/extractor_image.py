from __future__ import annotations

from pathlib import Path
import re

from models import ExtractionResult


def _imread_unicode(path: Path):
    """cv2.imread replacement that handles Unicode/Korean paths on Windows."""
    import cv2
    import numpy as np

    buf = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return image


def extract_image(path: Path, config: dict, max_chars: int = 8000) -> ExtractionResult:
    """Extract text from a JPG/JPEG/TIF/TIFF image file via Tesseract OCR."""
    try:
        import pytesseract
        from ocr_advanced import (
            _preprocess_image,
            _choose_best_text,
            _resolve_tesseract_cmd,
        )

        ocr_cfg = config.get("ocr", {})
        advanced_cfg = config.get("ocr", {}).get("advanced", {})
        lang = str(advanced_cfg.get("language", ocr_cfg.get("language", "kor+eng")))
        min_len = int(advanced_cfg.get("min_len", 40))
        tesseract_cmd = _resolve_tesseract_cmd(advanced_cfg)
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        image = _imread_unicode(path)
        if image is None:
            return ExtractionResult(
                file_type="image",
                extraction_status="error:cannot_read_image",
                ocr_quality_low=True,
                notes=[f"Could not read image file (possibly unsupported format or corrupt): {path.name}"],
            )

        # 1순위: Intel 가속(OpenVINO/RapidOCR). 미가용/실패 시 Tesseract로 폴백.
        engine = str(ocr_cfg.get("engine", "tesseract")).lower()
        text, conf, psm, used_crop = "", 0.0, 0, False
        engine_note = "Tesseract"
        if engine == "accelerated":
            from ocr_accelerated import ocr_image_array
            accel = ocr_image_array(image, config.get("ocr", {}).get("accelerated", {}))
            if accel is not None and accel[0].strip():
                text, conf = accel
                engine_note = "OpenVINO/RapidOCR"

        if not text.strip():
            preprocessed = _preprocess_image(image)
            text, conf, psm, used_crop, _ = _choose_best_text(preprocessed, min_len=min_len, lang=lang)

        threshold = int(ocr_cfg.get("force_ocr_threshold_chars", 80))
        norm = re.sub(r"\s+", "", text or "")
        quality_low = len(norm) < threshold

        if not text.strip():
            return ExtractionResult(
                file_type="image",
                extraction_status="empty_text",
                ocr_used=True,
                ocr_quality_low=True,
                notes=["OCR produced no text from image."],
            )

        return ExtractionResult(
            file_type="image",
            extraction_status="success",
            extracted_text=text,
            text_excerpt=text[:max_chars],
            page_count=1,
            ocr_used=True,
            ocr_quality_low=quality_low,
            notes=[f"Image OCR via {engine_note}. psm={psm} conf={conf:.1f} cropped={used_crop}"],
        )

    except ModuleNotFoundError as exc:
        return ExtractionResult(
            file_type="image",
            extraction_status=f"missing_dependency:{exc}",
            notes=["Required dependency missing. Run: pip install opencv-python pytesseract"],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="image",
            extraction_status=f"error:{exc}",
            notes=["Image extraction failed."],
        )
