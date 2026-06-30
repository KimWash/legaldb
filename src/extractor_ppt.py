from __future__ import annotations

import sys
from pathlib import Path
from models import ExtractionResult
from com_lock import ppt_lock

def extract_ppt(path: str | Path, max_chars: int = 8000) -> ExtractionResult:
    path = Path(path)
    if sys.platform != "win32":
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=["Legacy .ppt extraction via COM is only supported on Windows."],
        )
        
    try:
        import win32com.client
    except ImportError:
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=["Legacy .ppt extraction failed. pywin32 not installed."],
        )
        
    ppt = None
    com_initialized = False
    try:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            pass
            
        with ppt_lock:
            ppt = win32com.client.Dispatch("PowerPoint.Application")
            try:
                ppt.Visible = False
            except Exception:
                pass
            
            pres = ppt.Presentations.Open(
                str(path.resolve()),
                ReadOnly=True,
                Untitled=False,
                WithWindow=False,
            )
            lines: list[str] = []
            slide_count = pres.Slides.Count
            for i in range(1, slide_count + 1):
                slide = pres.Slides(i)
                for j in range(1, slide.Shapes.Count + 1):
                    try:
                         shape = slide.Shapes(j)
                         if shape.HasTextFrame:
                             text = shape.TextFrame.TextRange.Text.strip()
                             if text:
                                 lines.append(text)
                    except Exception:
                        pass
            pres.Close()
            
        combined = "\n".join(lines).strip()
        if not combined:
            return ExtractionResult(
                file_type="ppt",
                extraction_status="empty_text",
                notes=["No text extracted from PPT via COM."],
            )
        return ExtractionResult(
            file_type="ppt",
            extraction_status="success",
            extracted_text=combined,
            text_excerpt=combined[:max_chars],
            page_count=slide_count,
            notes=[f"PPT text extracted via COM. {slide_count} slides."],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=[f"Legacy .ppt extraction failed (possibly DRM protected): {exc}"],
        )
    finally:
        if ppt is not None:
            try:
                with ppt_lock:
                    ppt.Quit()
            except Exception:
                pass
        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
