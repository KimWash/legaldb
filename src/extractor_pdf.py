from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
import re
import warnings

from models import ExtractionResult
from ocr_advanced import run_advanced_ocr
from ocr_runner import run_ocrmypdf


def _read_pdf_text(path: Path, max_chars: int = 8000) -> tuple[str, int]:
    from pypdf import PdfReader

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*wrong pointing object.*")
        warnings.filterwarnings("ignore", message=".*invalid object.*")
        reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            chunks.append(text)
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    return "\n".join(chunks).strip()[:max_chars], len(reader.pages)


def _extract_with_advanced_ocr(path: Path, config: dict, temp_dir: Path, max_chars: int) -> ExtractionResult:
    advanced_cfg = dict(config.get("ocr", {}).get("advanced", {}))
    result = run_advanced_ocr(path, temp_dir=temp_dir, config=advanced_cfg)
    norm = re.sub(r"\s+", "", result.text or "")
    threshold = int(config["ocr"].get("force_ocr_threshold_chars", 80))
    return ExtractionResult(
        file_type="pdf-image",
        extraction_status="success" if result.status == "success" else f"advanced_ocr:{result.status}",
        extracted_text=result.text,
        text_excerpt=(result.text or "")[:max_chars],
        page_count=result.total_pages,
        ocr_used=True,
        ocr_quality_low=(len(norm) < threshold) or bool(result.failed_pages),
        notes=[
            "Advanced OCR pipeline used.",
            f"Advanced OCR run dir: {result.run_dir}",
            f"Advanced OCR mean confidence: {result.mean_confidence}",
            f"Advanced OCR failed pages: {result.failed_pages}",
        ],
    )


def _extract_with_ocrmypdf(path: Path, config: dict, temp_dir: Path, max_chars: int, page_count_hint: int) -> ExtractionResult:
    with NamedTemporaryFile(dir=temp_dir, suffix=".pdf", delete=False) as handle:
        ocr_output = Path(handle.name)
    ok, status = run_ocrmypdf(
        source_pdf=path,
        output_pdf=ocr_output,
        language=config["ocr"].get("language", "kor+eng"),
        skip_text=bool(config["ocr"].get("skip_text", True)),
        timeout_seconds=int(config["ocr"].get("timeout_seconds", 300)),
    )
    if not ok:
        return ExtractionResult(
            file_type="pdf-image",
            extraction_status=status,
            extracted_text="",
            text_excerpt="",
            page_count=page_count_hint,
            ocr_used=True,
            ocr_quality_low=True,
            notes=["OCRmyPDF attempted but failed."],
        )
    ocr_text, ocr_page_count = _read_pdf_text(ocr_output, max_chars=max_chars)
    try:
        ocr_output.unlink(missing_ok=True)
    except Exception:
        pass
    norm = re.sub(r"\s+", "", ocr_text or "")
    threshold = int(config["ocr"].get("force_ocr_threshold_chars", 80))
    return ExtractionResult(
        file_type="pdf-image",
        extraction_status="success" if ocr_text else "ocr_empty_text",
        extracted_text=ocr_text,
        text_excerpt=ocr_text[:max_chars],
        page_count=ocr_page_count,
        ocr_used=True,
        ocr_quality_low=len(norm) < threshold,
        notes=["OCRmyPDF text layer used."],
    )


def extract_pdf(path: Path, config: dict, temp_dir: Path, max_chars: int = 8000) -> ExtractionResult:
    threshold = int(config["ocr"].get("force_ocr_threshold_chars", 80))
    ocr_enabled = bool(config["ocr"].get("enabled", True))
    advanced_enabled = bool(config.get("ocr", {}).get("advanced", {}).get("enabled", True))
    fallback_to_ocrmypdf = bool(config.get("ocr", {}).get("advanced", {}).get("fallback_to_ocrmypdf", True))

    try:
        extracted_text, page_count = _read_pdf_text(path, max_chars=max_chars)
        if len(re.sub(r"\s+", "", extracted_text)) >= threshold:
            return ExtractionResult(
                file_type="pdf-text",
                extraction_status="success",
                extracted_text=extracted_text,
                text_excerpt=extracted_text[:max_chars],
                page_count=page_count,
                notes=["PDF text layer used."],
            )

        if not ocr_enabled:
            return ExtractionResult(
                file_type="pdf-image",
                extraction_status="insufficient_text_no_ocr",
                extracted_text=extracted_text,
                text_excerpt=extracted_text[:max_chars],
                page_count=page_count,
                ocr_quality_low=True,
                notes=["OCR disabled and PDF text was insufficient."],
            )

        if advanced_enabled:
            advanced_result = _extract_with_advanced_ocr(path, config=config, temp_dir=temp_dir, max_chars=max_chars)
            if advanced_result.extraction_status == "success":
                return advanced_result
            if fallback_to_ocrmypdf:
                fallback = _extract_with_ocrmypdf(path, config=config, temp_dir=temp_dir, max_chars=max_chars, page_count_hint=page_count)
                fallback.notes.append(f"Advanced OCR failed first: {advanced_result.extraction_status}")
                return fallback
            return advanced_result

        return _extract_with_ocrmypdf(path, config=config, temp_dir=temp_dir, max_chars=max_chars, page_count_hint=page_count)

    except ModuleNotFoundError as exc:
        return ExtractionResult(file_type="pdf", extraction_status=f"missing_dependency:{exc}", notes=["Required PDF/OCR dependency is not installed."])
    except Exception as exc:
        return ExtractionResult(file_type="pdf", extraction_status=f"error:{exc}", notes=["PDF extraction failed."])
