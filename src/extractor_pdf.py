from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
import io
import logging
import os
import re
import sys
import time
import warnings

from models import ExtractionResult
from ocr_advanced import run_advanced_ocr
from ocr_accelerated import run_accelerated_ocr
from ocr_runner import run_ocrmypdf

# Suppress pypdf warnings and logger warnings for advanced encodings
warnings.filterwarnings("ignore", message=".*Advanced encoding.*")
logging.getLogger("pypdf").setLevel(logging.ERROR)

# Patch pypdf to support common Korean and Japanese PDF encodings (CMaps)
try:
    import pypdf._cmap
    pypdf._cmap._predefined_cmap.update({
        "/UniKS-UTF16-H": "utf-16-be",
        "/UniKS-UTF16-V": "utf-16-be",
        "/UniKS-UCS2-H": "utf-16-be",
        "/UniKS-UCS2-V": "utf-16-be",
        "/KSC-EUC-H": "euc-kr",
        "/KSC-EUC-V": "euc-kr",
        "/KSCms-UHC-H": "cp949",
        "/KSCms-UHC-V": "cp949",
        "/KSCms-UHC-HW-H": "cp949",
        "/KSCms-UHC-HW-V": "cp949",
        "/UniJIS-UTF16-H": "utf-16-be",
        "/UniJIS-UTF16-V": "utf-16-be",
        "/UniJIS-UCS2-H": "utf-16-be",
        "/UniJIS-UCS2-V": "utf-16-be",
        "/90ms-RKSJ-H": "cp932",
        "/90ms-RKSJ-V": "cp932",
        "/90msp-RKSJ-H": "cp932",
        "/EUC-H": "euc-jp",
        "/EUC-V": "euc-jp",
    })
except Exception:
    pass



def _read_pdf_text(path: Path, max_chars: int = 8000) -> tuple[str, int]:
    from pypdf import PdfReader

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*wrong pointing object.*")
        warnings.filterwarnings("ignore", message=".*invalid object.*")
        _devnull = open(os.devnull, "w")
        _old_stderr = sys.stderr
        sys.stderr = _devnull
        try:
            reader = PdfReader(str(path))
        finally:
            sys.stderr = _old_stderr
            _devnull.close()
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
    timeout_secs = int(advanced_cfg.get("timeout_seconds", 0))
    result = run_advanced_ocr(path, temp_dir=temp_dir, config=advanced_cfg, timeout_seconds=timeout_secs)
    norm = re.sub(r"\s+", "", result.text or "")
    threshold = int(config["ocr"].get("force_ocr_threshold_chars", 80))
    # timeout_partial = 타임아웃 내에 처리된 페이지에서 텍스트를 얻은 경우 → 유효한 결과로 처리
    is_success = result.status in ("success", "timeout_partial") and bool(result.text)
    return ExtractionResult(
        file_type="pdf-image",
        extraction_status="success" if is_success else f"advanced_ocr:{result.status}",
        extracted_text=result.text,
        text_excerpt=(result.text or "")[:max_chars],
        page_count=result.total_pages,
        ocr_used=True,
        ocr_quality_low=(len(norm) < threshold) or bool(result.failed_pages),
        ocr_mean_confidence=float(result.mean_confidence or 0.0),
        notes=[
            f"Advanced OCR pipeline used (status={result.status}).",
            f"Advanced OCR run dir: {result.run_dir}",
            f"Advanced OCR mean confidence: {result.mean_confidence}",
            f"Advanced OCR failed pages: {result.failed_pages}",
        ],
    )


def _extract_with_accelerated_ocr(path: Path, config: dict, temp_dir: Path, max_chars: int) -> ExtractionResult:
    """OpenVINO/RapidOCR (Intel Arc GPU/NPU) backend. status='advanced_ocr:unavailable' 시 폴백 신호."""
    accel_cfg = dict(config.get("ocr", {}).get("accelerated", {}))
    timeout_secs = int(accel_cfg.get("timeout_seconds", config.get("ocr", {}).get("advanced", {}).get("timeout_seconds", 0)))
    result = run_accelerated_ocr(path, temp_dir=temp_dir, config=accel_cfg, timeout_seconds=timeout_secs)
    norm = re.sub(r"\s+", "", result.text or "")
    threshold = int(config["ocr"].get("force_ocr_threshold_chars", 80))
    is_success = result.status in ("success", "timeout_partial") and bool(result.text)
    return ExtractionResult(
        file_type="pdf-image",
        extraction_status="success" if is_success else f"accelerated_ocr:{result.status}",
        extracted_text=result.text,
        text_excerpt=(result.text or "")[:max_chars],
        page_count=result.total_pages,
        ocr_used=True,
        ocr_quality_low=(len(norm) < threshold) or bool(result.failed_pages),
        ocr_mean_confidence=float(result.mean_confidence or 0.0),
        notes=[
            f"Accelerated OCR (OpenVINO/RapidOCR) used (status={result.status}).",
            f"Accelerated OCR run dir: {result.run_dir}",
            f"Accelerated OCR mean confidence: {result.mean_confidence}",
            f"Accelerated OCR failed pages: {result.failed_pages}",
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
    # Windows 파일 잠금 대응: 삭제 실패 시 최대 5회 재시도 (100ms 간격)
    _max_retries = 5
    for _attempt in range(_max_retries):
        try:
            ocr_output.unlink(missing_ok=True)
            break
        except (PermissionError, OSError) as _e:
            if _attempt < _max_retries - 1:
                time.sleep(0.1)
            else:
                print(f"[extractor_pdf] 임시 파일 삭제 최종 실패 (무시): {ocr_output} - {_e}")
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
    engine = str(config.get("ocr", {}).get("engine", "tesseract")).lower()
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

        # 1순위: Intel 가속(OpenVINO/RapidOCR). 미가용/실패 시 Tesseract(advanced)→ocrmypdf 폴백.
        if engine == "accelerated":
            accel_result = _extract_with_accelerated_ocr(path, config=config, temp_dir=temp_dir, max_chars=max_chars)
            if accel_result.extraction_status == "success":
                return accel_result
            accel_status = accel_result.extraction_status

        if advanced_enabled:
            advanced_result = _extract_with_advanced_ocr(path, config=config, temp_dir=temp_dir, max_chars=max_chars)
            if engine == "accelerated":
                advanced_result.notes.append(f"Accelerated OCR fell back: {accel_status}")
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
