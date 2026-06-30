from __future__ import annotations

from pathlib import Path
from models import ExtractionResult
from vendor.hwp_reader import HWPReader, HWPXReader

def extract_hwp(path: str | Path, max_chars: int = 8000) -> ExtractionResult:
    path = Path(path)
    ext = path.suffix.lower()
    
    # HWP 3.0 (HWP 97/2002) legacy format detection
    try:
        if path.exists():
            with open(path, "rb") as f:
                header = f.read(32)
            if header.startswith(b"HWP Document File V3.00"):
                return ExtractionResult(
                    file_type="hwp",
                    extraction_status="manual_review_required",
                    notes=["Legacy HWP 3.0 format is not programmatically readable on systems without Hancom Office COM interface. Please convert to HWP 5.0 or HWPX."],
                )
    except Exception:
        pass
        
    try:
        if ext == ".hwpx":
            reader_class = HWPXReader
            file_type = "hwpx"
        elif ext == ".hwp":
            reader_class = HWPReader
            file_type = "hwp"
        else:
            return ExtractionResult(
                file_type="hwp",
                extraction_status="error",
                notes=[f"Unsupported extension for HWP extractor: {ext}"],
            )
            
        with reader_class(str(path)) as reader:
            text = reader.get_full_text()
            
        if not text:
            return ExtractionResult(
                file_type=file_type,
                extraction_status="empty_text",
                notes=[f"No text extracted from {file_type.upper()}."],
            )
            
        return ExtractionResult(
            file_type=file_type,
            extraction_status="success",
            extracted_text=text,
            text_excerpt=text[:max_chars],
            notes=[f"{file_type.upper()} text extracted."],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type=ext.lstrip(".") or "hwp",
            extraction_status="manual_review_required",
            notes=[f"HWP/HWPX extraction failed (possibly DRM or legacy version): {exc}"],
        )
