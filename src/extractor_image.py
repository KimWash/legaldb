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
    """Extract text from a JPG/JPEG/TIF/TIFF image file via OpenVINO/RapidOCR."""
    try:
        from ocr_accelerated import ocr_image_array

        image = _imread_unicode(path)
        if image is None:
            return ExtractionResult(
                file_type="image",
                extraction_status="error:cannot_read_image",
                ocr_quality_low=True,
                notes=[f"Could not read image file (possibly unsupported format or corrupt): {path.name}"],
            )

        # 무조건 Intel 가속(OpenVINO/RapidOCR) 엔진 사용. GPU/NPU 미가용 시 CPU로 자동 폴백됨.
        ocr_cfg = config.get("ocr", {})
        accel = ocr_image_array(image, ocr_cfg.get("accelerated", {}))
        
        text = ""
        conf = 0.0
        if accel is not None:
            text, conf = accel

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
            notes=[f"Image OCR via OpenVINO/RapidOCR. conf={conf:.1f}"],
        )

    except ModuleNotFoundError as exc:
        return ExtractionResult(
            file_type="image",
            extraction_status=f"missing_dependency:{exc}",
            notes=["Required dependency missing. Run: pip install opencv-python"],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="image",
            extraction_status=f"error:{exc}",
            notes=["Image extraction failed."],
        )
