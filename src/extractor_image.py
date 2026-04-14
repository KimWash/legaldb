from __future__ import annotations

from pathlib import Path
import subprocess

from models import ExtractionResult


def extract_image(path: Path, config: dict, max_chars: int = 8000) -> ExtractionResult:
    """Extract text from a JPG/JPEG image file via Tesseract OCR."""
    try:
        import cv2
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

        try:
            subprocess.run(
                [tesseract_cmd, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            return ExtractionResult(
                file_type="image",
                extraction_status=f"tesseract_not_available:{exc}",
                ocr_quality_low=True,
                notes=["Tesseract is not available."],
            )

        image = cv2.imread(str(path))
        if image is None:
            return ExtractionResult(
                file_type="image",
                extraction_status="error:cannot_read_image",
                ocr_quality_low=True,
                notes=["cv2 could not read the image file."],
            )

        preprocessed = _preprocess_image(image)
        text, conf, psm, used_crop, _ = _choose_best_text(preprocessed, min_len=min_len, lang=lang)

        threshold = int(ocr_cfg.get("force_ocr_threshold_chars", 80))
        import re
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
            notes=[
                f"Image OCR via Tesseract. psm={psm} conf={conf:.1f} cropped={used_crop}"
            ],
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
