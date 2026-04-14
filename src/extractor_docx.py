from __future__ import annotations

from pathlib import Path

from models import ExtractionResult


def extract_docx(path: Path, max_chars: int = 8000) -> ExtractionResult:
    try:
        from docx import Document

        document = Document(path)
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
        table_lines: list[str] = []
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
                if cells:
                    table_lines.append(" | ".join(cells))
        combined = "\n".join(paragraphs + table_lines).strip()
        if not combined:
            return ExtractionResult(file_type="docx", extraction_status="empty_text", notes=["No text extracted from DOCX."])
        return ExtractionResult(
            file_type="docx",
            extraction_status="success",
            extracted_text=combined,
            text_excerpt=combined[:max_chars],
            notes=["DOCX text extracted."],
        )
    except ModuleNotFoundError:
        return ExtractionResult(file_type="docx", extraction_status="missing_dependency:python-docx", notes=["python-docx is not installed."])
    except Exception as exc:
        return ExtractionResult(file_type="docx", extraction_status=f"error:{exc}", notes=["DOCX extraction failed."])
