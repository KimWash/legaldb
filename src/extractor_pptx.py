from __future__ import annotations

from pathlib import Path

from models import ExtractionResult


def extract_pptx(path: Path, max_chars: int = 8000) -> ExtractionResult:
    try:
        from pptx import Presentation
        from pptx.util import Pt

        prs = Presentation(str(path))
        lines: list[str] = []

        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_lines: list[str] = []

            # Title first
            if slide.shapes.title and slide.shapes.title.has_text_frame:
                title_text = slide.shapes.title.text_frame.text.strip()
                if title_text:
                    slide_lines.append(f"[슬라이드 {slide_num}] {title_text}")

            # Body text from all shapes
            for shape in slide.shapes:
                if shape == slide.shapes.title:
                    continue
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text.strip()
                        if text:
                            slide_lines.append(text)
                # Table content
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if cells:
                            slide_lines.append(" | ".join(cells))

            # Speaker notes
            if slide.has_notes_slide:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    slide_lines.append(f"[노트] {notes_text}")

            lines.extend(slide_lines)

        combined = "\n".join(lines).strip()
        if not combined:
            return ExtractionResult(
                file_type="pptx",
                extraction_status="empty_text",
                notes=["No text extracted from PPTX."],
            )
        return ExtractionResult(
            file_type="pptx",
            extraction_status="success",
            extracted_text=combined,
            text_excerpt=combined[:max_chars],
            page_count=len(prs.slides),
            notes=[f"PPTX text extracted. {len(prs.slides)} slides."],
        )
    except ModuleNotFoundError:
        return ExtractionResult(
            file_type="pptx",
            extraction_status="missing_dependency:python-pptx",
            notes=["python-pptx is not installed. Run: pip install python-pptx"],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="pptx",
            extraction_status=f"error:{exc}",
            notes=["PPTX extraction failed."],
        )
