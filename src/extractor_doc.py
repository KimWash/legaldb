from __future__ import annotations

import sys
from pathlib import Path
from models import ExtractionResult
from com_lock import word_lock

def extract_doc(path: str | Path, max_chars: int = 8000) -> ExtractionResult:
    path = Path(path)
    if sys.platform != "win32":
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=["Legacy .doc extraction via COM is only supported on Windows."],
        )
        
    try:
        import win32com.client
    except ImportError:
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=["Legacy .doc extraction failed. pywin32 not installed."],
        )
        
    word = None
    com_initialized = False
    try:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            pass
            
        with word_lock:
            word = win32com.client.Dispatch("Word.Application")
            try:
                word.Visible = False
            except Exception:
                pass
            try:
                word.DisplayAlerts = 0  # wdAlertsNone
            except Exception:
                pass
            
            doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)
            raw = doc.Range().Text
            doc.Close(False)
            
        combined = raw.replace("\r", "\n").strip()
        if not combined:
            return ExtractionResult(
                file_type="doc",
                extraction_status="empty_text",
                notes=["No text extracted from DOC via COM."],
            )
        return ExtractionResult(
            file_type="doc",
            extraction_status="success",
            extracted_text=combined,
            text_excerpt=combined[:max_chars],
            notes=["DOC text extracted via COM."],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=[f"Legacy .doc extraction failed (possibly DRM protected): {exc}"],
        )
    finally:
        if word is not None:
            try:
                with word_lock:
                    word.Quit()
            except Exception:
                pass
        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
