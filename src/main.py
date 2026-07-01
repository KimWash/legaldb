from __future__ import annotations

from pathlib import Path
import argparse
from dataclasses import asdict
import hashlib
import json

# ── Global surrogate cleaning patch for JSON serialization ───────────────────
def clean_surrogates(val):
    if isinstance(val, str):
        return "".join(c if not (0xD800 <= ord(c) <= 0xDFFF) else "\uFFFD" for c in val)
    elif isinstance(val, dict):
        return {clean_surrogates(k): clean_surrogates(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [clean_surrogates(x) for x in val]
    elif isinstance(val, tuple):
        return tuple(clean_surrogates(x) for x in val)
    elif isinstance(val, set):
        return {clean_surrogates(x) for x in val}
    return val

_original_dumps = json.dumps
def _safe_dumps(obj, *args, **kwargs):
    try:
        cleaned = clean_surrogates(obj)
    except Exception:
        cleaned = obj
    return _original_dumps(cleaned, *args, **kwargs)
json.dumps = _safe_dumps
# ─────────────────────────────────────────────────────────────────────────────
import os
import re
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import BoundedSemaphore
import threading

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from config_loader import ensure_directories, load_config, resolve_project_path
from excel_writer import read_review_rows, write_review_workbook
from extractor_doc import extract_doc
from extractor_docx import extract_docx
from extractor_eml import extract_eml
from extractor_hwp import extract_hwp
from extractor_image import extract_image
from extractor_pdf import extract_pdf
from extractor_ppt import extract_ppt
from extractor_pptx import extract_pptx
from extractor_xlsx import extract_xlsx
from llm_client import OllamaClient
from models import AnalysisRecord, ExtractionResult, NamingResult
from naming_engine import infer_case_name, infer_doc_type, infer_institution, is_rename_scope, mark_conflicts, normalize_date, propose_name
from rename_executor import execute_rename
from rollback_executor import execute_rollback
from scanner import scan_files, scan_sharepoint_files
from sharepoint_client import SharePointClient

SUMMARY_SIGNAL_PATTERN = re.compile(
    r"(agreement|contract|notice|claim|arbitration|opinion|report|memo|"
    r"계약|합의|소송|중재|의견|보고|통지|결의|증명|등기|양해각서|수정계약서)",
    re.IGNORECASE,
)
CACHE_SCHEMA_VERSION = 11

_LEGAL_META_KEYS = [
    "case_name_normalized", "case_alias", "case_type", "dispute_type",
    "document_category", "document_type_normalized", "procedure_stage",
    "document_purpose", "legal_issue_primary", "legal_issue_secondary",
    "issue_tags", "claim_type", "party_our_side", "party_counterparty",
    "party_role", "law_firm_name_normalized", "institution_role",
    "country_region", "amount_mentioned", "claim_amount", "currency",
    "amount_context", "event_date", "date_type", "next_action_date",
    "timeline_summary", "lawyer_summary", "search_summary",
    "recommended_use", "review_priority", "review_priority_reason",
    "metadata_limitations", "needs_legal_review",
]


def _extract_legal_metadata(llm_result: dict | None) -> dict:
    if not llm_result:
        return {}
    return {k: llm_result[k] for k in _LEGAL_META_KEYS if k in llm_result}


def _format_seconds(total_seconds: float) -> str:
    secs = max(0, int(total_seconds))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _build_timing_suffix(start_ts: float, processed: int, total: int) -> str:
    elapsed = max(0.0, time.monotonic() - start_ts)
    if processed <= 0 or total <= 0:
        return f"elapsed={_format_seconds(elapsed)} eta=--:--"
    rate = processed / max(elapsed, 1e-6)
    remaining = max(0, total - processed)
    eta = remaining / max(rate, 1e-6)
    return f"elapsed={_format_seconds(elapsed)} eta={_format_seconds(eta)}"


def _is_clean_sentence(sentence: str) -> bool:
    s = re.sub(r"\s+", " ", sentence or "").strip()
    if len(s) < 12:
        return False
    if re.search(r"(dear|best regards|sincerely|from:|to:|subject:)", s, re.IGNORECASE):
        return False
    if re.search(r"[\|`~^_=]{2,}", s):
        return False
    token_count = len(re.findall(r"[A-Za-z가-힣0-9]+", s))
    if token_count < 4:
        return False
    return True


def _score_sentence(sentence: str) -> tuple[int, int, int]:
    s = sentence
    keyword_score = 3 if SUMMARY_SIGNAL_PATTERN.search(s) else 0
    length_score = max(0, 45 - abs(len(s) - 34))
    alpha_num_score = len(re.findall(r"[A-Za-z가-힣0-9]", s))
    return (keyword_score, length_score, alpha_num_score)


def _build_llm_excerpt(source_text: str, max_chars: int) -> str:
    """Build an LLM-friendly excerpt that always front-loads the document beginning.

    Legal documents concentrate the most identifying information (parties, subject,
    legal action) in the first section. We reserve 60% of the budget for the
    document head and fill the remaining 40% with high-scoring sentences from the
    rest of the body.
    """
    text = re.sub(r"\s+", " ", source_text or "").strip()
    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    head_budget = max(400, int(max_chars * 0.6))
    tail_budget = max_chars - head_budget

    # Always include the document beginning.
    head = text[:head_budget]

    # Pick informative sentences from the remainder.
    remainder = text[head_budget:]
    candidates = re.split(r"(?<=[.!?。！？])\s+|\n+", remainder)
    ranked: list[tuple[tuple[int, int, int], str]] = []
    seen: set[str] = set()
    for raw in candidates:
        s = re.sub(r"\s+", " ", raw).strip()
        if not _is_clean_sentence(s):
            continue
        norm = re.sub(r"[^a-z0-9가-힣]+", "", s.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ranked.append((_score_sentence(s), s))

    ranked.sort(key=lambda item: item[0], reverse=True)
    extra_parts: list[str] = []
    used = 0
    for _, sentence in ranked:
        if used + len(sentence) + 1 > tail_budget:
            continue
        extra_parts.append(sentence)
        used += len(sentence) + 1
        if used >= tail_budget * 0.9:
            break

    if extra_parts:
        return f"{head}\n...\n" + "\n".join(extra_parts)
    return head


def _cache_file_path(project_root: Path) -> Path:
    return project_root / "temp" / "analysis_cache.json"


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
        if int(data.get("schema_version", 0)) != CACHE_SCHEMA_VERSION:
            return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
        if not isinstance(data.get("entries"), dict):
            data["entries"] = {}
        return data
    except Exception:
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}


def _save_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_cache_key(record, config: dict, perf: dict) -> str:
    sig = {
        "path": record.original_full_path,
        "size": record.file_size,
        "mtime": record.last_modified_time,
        "llm_provider": config.get("llm", {}).get("provider", "gemini"),
        "llm_model": config.get("llm", {}).get("gemini", {}).get("model", ""),
        "llm_primary_model": config.get("llm", {}).get("primary_model", ""),
        "excerpt_chars": perf.get("llm_excerpt_chars", 0),
        "extract_max_chars": perf.get("extract_max_chars", 0),
        "ocr_enabled": bool(config.get("ocr", {}).get("enabled", True)),
        "advanced_ocr_enabled": bool(config.get("ocr", {}).get("advanced", {}).get("enabled", True)),
    }
    raw = json.dumps(sig, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _should_call_llm(record, extraction: ExtractionResult, use_llm: bool) -> bool:
    if not use_llm:
        return False
    if extraction.extraction_status != "success":
        return False
    if not extraction.text_excerpt:
        return False
    # Always call LLM when text is available — summary quality requires reading actual document body.
    return True


def _build_analysis_result(record, extraction: ExtractionResult, llm_result: dict | None, config: dict, llm_client=None) -> AnalysisRecord:
    # 범위 밖('4. 기타문서' 포함) 파일은 (지원 여부와 무관하게) 원본명 유지 — 캐시 적중/신규 경로 동일 처리
    if not is_rename_scope(record):
        naming = NamingResult(
            suggested_file_name=record.original_file_name,
            suggested_full_path=record.original_full_path,
            reason="개명 제외 대상 폴더('4. 기타문서')에 속하여 원본 파일명을 유지합니다.",
            confidence=0.0,
            needs_manual_review=False,
            rename_status="out_of_scope",
            rollback_name=record.original_file_name,
        )
    else:
        naming = propose_name(record, extraction, llm_result, config, llm_client=llm_client)
        if extraction.extraction_status == "manual_review_required":
            ext = record.file_extension.lower()
            if ext in (".hwp", ".hwpx"):
                if any("Legacy HWP 3.0" in note for note in extraction.notes):
                    naming.reason = "구 버전 한글 문서 형식(HWP 3.0)으로 수동 검토 또는 변환이 필요합니다. (HWP 5.0/HWPX로 변환 요망)"
                else:
                    naming.reason = "한글 문서 형식(.hwp/.hwpx)으로 수동 검토 또는 변환이 필요합니다. (DRM 또는 파일 손상)"
            elif ext in (".doc", ".ppt"):
                naming.reason = f"구 버전 문서 형식({ext})으로 수동 검토 또는 변환이 필요합니다. (DRM 또는 파일 손상)"
            else:
                naming.reason = f"수동 검토 또는 변환이 필요한 문서 형식({ext})입니다."
            naming.needs_manual_review = True
            naming.confidence = 0.0
            print(f"[warning] {record.original_file_name} is flagged for manual review: {naming.reason}")

    legal_metadata = _extract_legal_metadata(llm_result)

    # Clean any surrogate characters to prevent UnicodeEncodeError in Excel/CSV/JSON serialization
    for field_name in record.__dataclass_fields__:
        val = getattr(record, field_name)
        if isinstance(val, str):
            setattr(record, field_name, clean_surrogates(val))
    for field_name in extraction.__dataclass_fields__:
        val = getattr(extraction, field_name)
        if isinstance(val, str):
            setattr(extraction, field_name, clean_surrogates(val))
        elif isinstance(val, list):
            setattr(extraction, field_name, clean_surrogates(val))
    for field_name in naming.__dataclass_fields__:
        val = getattr(naming, field_name)
        if isinstance(val, str):
            setattr(naming, field_name, clean_surrogates(val))
    legal_metadata = clean_surrogates(legal_metadata)

    return AnalysisRecord(record, extraction, naming, legal_metadata)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Legal DB local folder rename PoC")
    parser.add_argument("--mode", required=True, choices=["analyze", "rename", "rollback", "survey"])
    parser.add_argument("--source", choices=["local", "sharepoint"], default="local",
                        help="File source: local filesystem (default) or SharePoint via MS Graph API")
    parser.add_argument("--config", default=str(CURRENT_DIR.parent / "config.yaml"))
    parser.add_argument("--review-file")
    parser.add_argument("--rollback-file")
    parser.add_argument("--fast", action="store_true", help="Disable OCR and LLM for faster draft analysis")
    parser.add_argument("--workers", type=int, help="Parallel workers for analyze mode")
    parser.add_argument("--max-files", type=int, help="Process only first N scanned files")
    parser.add_argument("--progress-every", type=int, default=10, help="Progress print interval")
    parser.add_argument("--llm-excerpt-chars", type=int, help="Trim excerpt length sent to LLM")
    parser.add_argument("--ocr-workers", type=int, help="Max concurrent OCR/PDF extraction workers")
    parser.add_argument("--llm-workers", type=int, help="Max concurrent LLM calls")
    parser.add_argument("--no-cache", action="store_true", help="Do not read/write analysis cache for this run")
    parser.add_argument("--clear-cache", action="store_true", help="Delete analysis cache before analyze")
    parser.add_argument("--site-url", help="SharePoint site URL (overrides config.yaml sharepoint.site_url)")
    parser.add_argument("--root-folder", help="Root folder path inside the document library (overrides config.yaml sharepoint.root_folder)")
    return parser.parse_args()


def _performance_config(config: dict, args: argparse.Namespace) -> dict:
    perf = config.get("performance", {})
    cpu_count = os.cpu_count() or 4
    default_workers = max(1, min(6, cpu_count))
    default_ocr_workers = max(1, min(2, default_workers))
    default_llm_workers = max(1, min(3, default_workers))
    return {
        "workers": max(1, int(args.workers or perf.get("workers", default_workers))),
        "max_files": int(args.max_files or perf.get("max_files", 0)),
        "progress_every": max(1, int(args.progress_every or perf.get("progress_every", 10))),
        "extract_max_chars": int(perf.get("extract_max_chars", 8000)),
        "llm_excerpt_chars": int(args.llm_excerpt_chars or perf.get("llm_excerpt_chars", 6000)),
        "ocr_workers": max(1, int(args.ocr_workers or perf.get("ocr_workers", default_ocr_workers))),
        "llm_workers": max(1, int(args.llm_workers or perf.get("llm_workers", default_llm_workers))),
        "heartbeat_seconds": int(perf.get("heartbeat_seconds", 30)),
        "fast": bool(args.fast),
        "fast_disables_ocr": bool(perf.get("fast_disables_ocr", True)),
        "fast_disables_llm": bool(perf.get("fast_disables_llm", True)),
    }


def _analyze_single_record(
    record,
    config: dict,
    temp_dir: Path,
    llm: OllamaClient | None,
    use_llm: bool,
    llm_excerpt_chars: int,
    extract_max_chars: int,
    cache_key: str,
    ocr_semaphore: BoundedSemaphore,
    llm_semaphore: BoundedSemaphore,
    sp_client: SharePointClient | None = None,
    sp_semaphore: BoundedSemaphore | None = None,
    active_files: dict | None = None,
) -> tuple[AnalysisRecord, str, dict]:
    tid = threading.get_ident()
    if active_files is not None:
        active_files[tid] = record.original_file_name
    try:
        return _analyze_single_record_inner(
            record, config, temp_dir, llm, use_llm, llm_excerpt_chars,
            extract_max_chars, cache_key, ocr_semaphore, llm_semaphore,
            sp_client, sp_semaphore,
        )
    finally:
        if active_files is not None:
            active_files.pop(tid, None)


def _analyze_single_record_inner(
    record,
    config: dict,
    temp_dir: Path,
    llm: OllamaClient | None,
    use_llm: bool,
    llm_excerpt_chars: int,
    extract_max_chars: int,
    cache_key: str,
    ocr_semaphore: BoundedSemaphore,
    llm_semaphore: BoundedSemaphore,
    sp_client: SharePointClient | None = None,
    sp_semaphore: BoundedSemaphore | None = None,
) -> tuple[AnalysisRecord, str, dict]:
    # 개명 대상은 경로에 '4. 기타문서'가 없는 파일뿐. 범위 밖이면 (지원 여부 무관) OCR/LLM 생략·원본명 유지.
    if not is_rename_scope(record):
        extraction = ExtractionResult(
            file_type=record.file_extension.lstrip(".") or "file",
            extraction_status="skipped_out_of_scope",
            notes=["개명 제외 대상 폴더('4. 기타문서')에 속하여 개명 대상에서 제외 (OCR/LLM 생략)."],
        )
        analysis = _build_analysis_result(record, extraction, None, config)
        cache_payload = {"extraction": asdict(extraction), "llm_result": None}
        return analysis, cache_key, cache_payload

    # 범위 안이지만 지원하지 않는 형식 → 내용을 못 읽으므로 파일명+폴더명 추론으로 제안.
    if not record.supported:
        extraction = ExtractionResult(
            file_type="unsupported",
            extraction_status="unsupported_inference",
            notes=["지원하지 않는 형식 — 파일명/폴더명 기반 추론으로 파일명 제안."],
        )
        analysis = _build_analysis_result(record, extraction, None, config)
        cache_payload = {"extraction": asdict(extraction), "llm_result": None}
        return analysis, cache_key, cache_payload

    # For SharePoint records, download the file to a local temp path before extraction.
    if record.sharepoint_item_id and sp_client is not None:
        sp_dl_dir = temp_dir / "sp_downloads"
        sem = sp_semaphore or BoundedSemaphore(1)
        with sem:
            local_file = sp_client.download_file(
                record.sharepoint_item_id, record.original_file_name, sp_dl_dir,
                web_url=record.sharepoint_web_url,
                drive_id=record.sharepoint_drive_id,
            )
        path = local_file
    else:
        path = Path(record.original_full_path)

    if record.file_extension == ".pdf":
        with ocr_semaphore:
            extraction = extract_pdf(path, config, temp_dir, max_chars=extract_max_chars)
    elif record.file_extension == ".docx":
        extraction = extract_docx(path, max_chars=extract_max_chars)
    elif record.file_extension == ".pptx":
        extraction = extract_pptx(path, max_chars=extract_max_chars)
    elif record.file_extension == ".ppt":
        extraction = extract_ppt(path, max_chars=extract_max_chars)
    elif record.file_extension in (".jpg", ".jpeg", ".tif", ".tiff"):
        with ocr_semaphore:
            extraction = extract_image(path, config, max_chars=extract_max_chars)
    elif record.file_extension in (".xlsx", ".xls"):
        extraction = extract_xlsx(path, max_chars=extract_max_chars)
    elif record.file_extension == ".eml":
        extraction = extract_eml(path, max_chars=extract_max_chars)
    elif record.file_extension in (".hwp", ".hwpx"):
        extraction = extract_hwp(path, max_chars=extract_max_chars)
    elif record.file_extension == ".doc":
        extraction = extract_doc(path, max_chars=extract_max_chars)
    else:
        extraction = ExtractionResult(
            file_type=record.file_extension.lstrip(".") or "file",
            extraction_status="unsupported",
            notes=["Unsupported file format."],
        )

    llm_result = None
    if llm is not None and _should_call_llm(record, extraction, use_llm):
        source_text = extraction.extracted_text or extraction.text_excerpt
        excerpt = _build_llm_excerpt(source_text, llm_excerpt_chars)
        with llm_semaphore:
            llm_result = llm.extract_metadata(
                {
                    "file_path": record.original_full_path,
                    "file_extension": record.file_extension,
                    "parent_folder": Path(record.original_dir_path).name,
                    "relative_path": record.relative_path_from_root,
                    "extracted_text_excerpt": excerpt,
                }
            )

    analysis = _build_analysis_result(record, extraction, llm_result, config, llm_client=llm)
    cache_payload = {"extraction": asdict(extraction), "llm_result": llm_result}
    return analysis, cache_key, cache_payload


def analyze(
    config: dict,
    project_root: Path,
    args: argparse.Namespace,
    sp_client: SharePointClient | None = None,
) -> int:
    perf = _performance_config(config, args)
    runtime_config = dict(config)
    runtime_config["ocr"] = dict(config.get("ocr", {}))
    runtime_config["llm"] = dict(config.get("llm", {}))
    if perf["fast"] and perf["fast_disables_ocr"]:
        runtime_config["ocr"]["enabled"] = False
    if perf["fast"] and perf["fast_disables_llm"]:
        runtime_config["llm"]["enabled"] = False

    temp_dir = resolve_project_path(project_root, config["temp"]["dir"])
    tempfile.tempdir = str(temp_dir)
    review_dir = resolve_project_path(project_root, config["review"]["output_dir"])
    logs_dir = resolve_project_path(project_root, config["logs"]["output_dir"])
    llm = OllamaClient(runtime_config, project_root) if runtime_config["llm"].get("enabled", True) else None
    cache_path = _cache_file_path(project_root)
    if args.clear_cache and cache_path.exists():
        cache_path.unlink(missing_ok=True)
    cache_enabled = not args.no_cache
    cache_data = _load_cache(cache_path) if cache_enabled else {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    cache_entries: dict = dict(cache_data.get("entries", {}))

    if args.source == "sharepoint":
        if sp_client is None:
            raise SystemExit("SharePoint 클라이언트가 초기화되지 않았습니다. --source sharepoint 옵션을 확인하세요.")
        print("[analyze] SharePoint 파일 목록 조회 중... (Sites.Read.All 권한 필요)")
        scanned_records = scan_sharepoint_files(sp_client, config)
        input_root_display = config.get("sharepoint", {}).get("site_url", "SharePoint")
    else:
        input_root = Path(config["input_root"])
        scanned_records = scan_files(input_root, config)
        input_root_display = str(input_root)

    if perf["max_files"] > 0:
        scanned_records = scanned_records[: perf["max_files"]]

    total_count = len(scanned_records)
    analysis_records: list[AnalysisRecord] = []
    uncached_records: list[tuple] = []
    print(
        f"[analyze] start total_files={total_count} workers={perf['workers']} "
        f"fast={perf['fast']} ocr={runtime_config['ocr'].get('enabled', True)} "
        f"llm={runtime_config['llm'].get('enabled', True)} "
        f"ocr_workers={perf['ocr_workers']} llm_workers={perf['llm_workers']} "
        f"cache_enabled={cache_enabled}"
    )

    processed_count = 0
    manual_review_count = 0
    cache_hit_count = 0
    start_ts = time.monotonic()

    for record in scanned_records:
        cache_key = _build_cache_key(record, runtime_config, perf)
        cached = cache_entries.get(cache_key) if cache_enabled else None
        if isinstance(cached, dict) and isinstance(cached.get("extraction"), dict):
            try:
                if cached["extraction"].get("extraction_status") == "skipped_out_of_scope" and is_rename_scope(record):
                    raise ValueError("Re-analyze since it is now in scope")
                extraction = ExtractionResult(**cached["extraction"])
                llm_result = cached.get("llm_result")
                result = _build_analysis_result(record, extraction, llm_result, runtime_config)
                result.extraction.extracted_text = ""  # Free heavy text data immediately
                analysis_records.append(result)
                processed_count += 1
                cache_hit_count += 1
                if result.naming.needs_manual_review:
                    manual_review_count += 1
                if (
                    processed_count == 1
                    or processed_count % perf["progress_every"] == 0
                    or processed_count == total_count
                ):
                    progress = (processed_count / total_count * 100) if total_count else 100.0
                    timing = _build_timing_suffix(start_ts, processed_count, total_count)
                    print(
                        f"[analyze] progress {processed_count}/{total_count} ({progress:.1f}%) "
                        f"manual_review={manual_review_count} cache_hits={cache_hit_count} {timing}"
                    )
                continue
            except Exception:
                pass
        uncached_records.append((record, cache_key))

    ocr_semaphore = BoundedSemaphore(perf["ocr_workers"])
    llm_semaphore = BoundedSemaphore(perf["llm_workers"])
    sp_semaphore = BoundedSemaphore(max(1, perf["workers"])) if sp_client else None
    active_files: dict[int, str] = {}  # thread_id → filename (GIL로 thread-safe)

    # Use a sliding window to limit active Futures in the executor queue (Backpressure)
    max_active = max(1, perf["workers"] * 2)
    uncached_iter = iter(uncached_records)
    future_to_record = {}
    pending = set()

    with ThreadPoolExecutor(max_workers=perf["workers"]) as executor:
        # Initial fill
        for record_and_key in uncached_iter:
            f = executor.submit(
                _analyze_single_record,
                record_and_key[0],
                runtime_config,
                temp_dir,
                llm,
                bool(runtime_config["llm"].get("enabled", True)),
                perf["llm_excerpt_chars"],
                perf["extract_max_chars"],
                record_and_key[1],
                ocr_semaphore,
                llm_semaphore,
                sp_client,
                sp_semaphore,
                active_files,
            )
            future_to_record[f] = record_and_key[0]
            pending.add(f)
            if len(pending) >= max_active:
                break

        last_heartbeat = time.monotonic()

        while pending:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)

            # Process completed tasks
            for future in done:
                record = future_to_record[future]
                try:
                    result, cache_key, cache_payload = future.result()
                    result.extraction.extracted_text = ""  # Free heavy text data immediately
                    if cache_enabled:
                        cache_entries[cache_key] = cache_payload
                except Exception as exc:
                    extraction = ExtractionResult(
                        file_type="error",
                        extraction_status=f"worker_error:{exc}",
                        notes=["Unhandled worker exception."],
                    )
                    naming = NamingResult(
                        reason=f"파일 분석 중 오류가 발생했습니다: {exc}",
                        confidence=0.0,
                        needs_manual_review=True,
                        rename_status="analysis_error",
                        rollback_name=record.original_file_name,
                    )
                    result = AnalysisRecord(record, extraction, naming)

                analysis_records.append(result)
                processed_count += 1
                if result.naming.needs_manual_review:
                    manual_review_count += 1

                if (
                    processed_count == 1
                    or processed_count % perf["progress_every"] == 0
                    or processed_count == total_count
                ):
                    progress = (processed_count / total_count * 100) if total_count else 100.0
                    timing = _build_timing_suffix(start_ts, processed_count, total_count)
                    print(
                        f"[analyze] progress {processed_count}/{total_count} ({progress:.1f}%) "
                        f"manual_review={manual_review_count} cache_hits={cache_hit_count} {timing}"
                    )
                
                # Explicitly clean up completed future references
                del future_to_record[future]

            # Refill the window
            while len(pending) < max_active:
                try:
                    record_and_key = next(uncached_iter)
                except StopIteration:
                    break
                f = executor.submit(
                    _analyze_single_record,
                    record_and_key[0],
                    runtime_config,
                    temp_dir,
                    llm,
                    bool(runtime_config["llm"].get("enabled", True)),
                    perf["llm_excerpt_chars"],
                    perf["extract_max_chars"],
                    record_and_key[1],
                    ocr_semaphore,
                    llm_semaphore,
                    sp_client,
                    sp_semaphore,
                    active_files,
                )
                future_to_record[f] = record_and_key[0]
                pending.add(f)

            if not done:
                now = time.monotonic()
                if now - last_heartbeat >= perf["heartbeat_seconds"]:
                    timing = _build_timing_suffix(start_ts, processed_count, total_count)
                    currently = list(active_files.values())
                    active_str = ", ".join(currently) if currently else "-"
                    print(
                        f"[analyze] heartbeat completed={processed_count}/{total_count} "
                        f"pending={len(pending)} {timing}\n"
                        f"          active: {active_str}"
                    )
                    last_heartbeat = now

    analysis_records.sort(key=lambda item: item.file_record.seq)
    if cache_enabled:
        _save_cache(cache_path, {"schema_version": CACHE_SCHEMA_VERSION, "entries": cache_entries})

    mark_conflicts(analysis_records)
    try:
        workbook_path = write_review_workbook(analysis_records, review_dir)
    except ModuleNotFoundError as exc:
        raise SystemExit(f"Missing dependency for Excel output: {exc}. Run `pip install -r requirements.txt`.") from exc

    summary = {
        "input_root": input_root_display,
        "total_files": len(analysis_records),
        "supported_files": sum(1 for item in analysis_records if item.file_record.supported),
        "manual_review_count": sum(1 for item in analysis_records if item.naming.needs_manual_review),
        "cache_hits": cache_hit_count,
        "review_workbook": str(workbook_path),
    }
    summary_path = logs_dir / f"analysis_summary_{workbook_path.stem.replace('rename_review_', '')}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def rename_mode(
    config: dict,
    project_root: Path,
    review_file: str | None,
    sp_client: SharePointClient | None = None,
) -> int:
    if not review_file:
        raise SystemExit("--review-file is required for rename mode")

    review_path = Path(review_file)
    if not review_path.is_absolute():
        review_path = (project_root / review_path).resolve()

    try:
        review_rows = read_review_rows(review_path)
    except ModuleNotFoundError as exc:
        raise SystemExit(f"Missing dependency for Excel input: {exc}. Run `pip install -r requirements.txt`.") from exc

    # Auto-initialize SharePoint client if SharePoint files are detected and sp_client is None
    approved_rows = [r for r in review_rows if str(r.get("approval") or "").strip().upper() == "Y"]
    has_sp = any(bool(str(r.get("sharepoint_item_id") or "").strip()) for r in approved_rows)
    if has_sp and sp_client is None:
        print("[rename] SharePoint 파일이 감지되었습니다. SharePoint 클라이언트를 초기화합니다...")
        try:
            sp_client = _build_sp_client(config, project_root)
        except Exception as exc:
            raise SystemExit(
                f"[rename] SharePoint 클라이언트 초기화 실패: {exc}\n"
                f"  config.yaml의 sharepoint 설정을 확인하거나 CLI 실행 시 --source sharepoint 옵션을 지정해 주세요."
            )

    try:
        result = execute_rename(
            review_rows=review_rows,
            config=config,
            logs_dir=resolve_project_path(project_root, config["logs"]["output_dir"]),
            rollback_dir=resolve_project_path(project_root, config["rollback"]["output_dir"]),
            sp_client=sp_client,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def rollback_mode(
    config: dict,
    project_root: Path,
    rollback_file: str | None,
    sp_client: SharePointClient | None = None,
) -> int:
    if not rollback_file:
        raise SystemExit("--rollback-file is required for rollback mode")

    rollback_path = Path(rollback_file)
    if not rollback_path.is_absolute():
        rollback_path = (project_root / rollback_path).resolve()

    if sp_client is None and rollback_path.exists():
        try:
            from rollback_executor import _load_rollback_items
            items = _load_rollback_items(rollback_path)
            has_sp = any(bool(str(it.get("sharepoint_item_id") or "").strip()) for it in items)
            if has_sp:
                print("[rollback] SharePoint 파일이 감지되었습니다. SharePoint 클라이언트를 초기화합니다...")
                sp_client = _build_sp_client(config, project_root)
        except Exception as exc:
            print(f"[rollback] SharePoint 클라이언트 초기화 중 오류 발생: {exc}")

    print(json.dumps(execute_rollback(rollback_path, sp_client=sp_client), ensure_ascii=False, indent=2))
    return 0


def survey_mode(config: dict, project_root: Path, sp_client: SharePointClient) -> int:
    from sp_survey import run_survey
    logs_dir = resolve_project_path(project_root, config["logs"]["output_dir"])
    return run_survey(sp_client, config, logs_dir)


def _build_sp_client(config: dict, project_root: Path, need_site_scan: bool = False) -> SharePointClient:
    sp_cfg = config.get("sharepoint", {})
    token_cache_path = resolve_project_path(
        project_root, sp_cfg.get("token_cache_path", "./temp/sp_token_cache.json")
    )
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate(need_site_scan=need_site_scan)
    return client


def main() -> int:
    args = parse_args()
    project_root = CURRENT_DIR.parent
    config = load_config(args.config)
    ensure_directories(config, project_root)

    # --site-url / --root-folder override config (useful for survey across multiple sites)
    if getattr(args, "site_url", None):
        config.setdefault("sharepoint", {})
        config["sharepoint"]["site_url"] = args.site_url
        # Clear per-site shortcuts so the new URL is resolved fresh
        config["sharepoint"].pop("drive_id", None)
        config["sharepoint"].pop("folder_sharing_url", None)
    if getattr(args, "root_folder", None) is not None:
        config.setdefault("sharepoint", {})
        config["sharepoint"]["root_folder"] = args.root_folder

    sp_client: SharePointClient | None = None
    if getattr(args, "source", "local") == "sharepoint" or args.mode == "survey":
        sp_cfg = config.get("sharepoint", {})
        has_shortcut = bool(sp_cfg.get("drive_id") or sp_cfg.get("folder_sharing_url"))
        need_scan = (args.mode in ("analyze", "survey")) and not has_shortcut
        sp_client = _build_sp_client(config, project_root, need_site_scan=need_scan)

    if args.mode == "survey":
        if sp_client is None:
            raise SystemExit(
                "[survey] SharePoint 클라이언트를 초기화할 수 없습니다.\n"
                "  config.yaml의 sharepoint 섹션을 확인하세요."
            )
        return survey_mode(config, project_root, sp_client)
    if args.mode == "analyze":
        return analyze(config, project_root, args, sp_client=sp_client)
    if args.mode == "rename":
        return rename_mode(config, project_root, args.review_file, sp_client=sp_client)
    return rollback_mode(config, project_root, args.rollback_file, sp_client=sp_client)


if __name__ == "__main__":
    raise SystemExit(main())
