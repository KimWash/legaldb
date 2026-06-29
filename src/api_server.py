from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time as _time

import requests as _requests
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from config_loader import ensure_directories, load_config, resolve_project_path
from excel_writer import write_review_workbook
from llm_client import OllamaClient
from main import (
    CACHE_SCHEMA_VERSION,
    _analyze_single_record,
    _build_cache_key,
    _cache_file_path,
    _load_cache,
    _save_cache,
)
from naming_engine import mark_conflicts
from rename_executor import execute_rename
from rollback_executor import execute_rollback, preview_rollback
from scanner import scan_files, scan_sharepoint_files
from sharepoint_client import SharePointClient
from sp_survey import build_survey_data, save_survey_cache, load_survey_cache, compute_delta_changes, SUPPORTED_EXTS_SET

_config = load_config(str(PROJECT_ROOT / "config.yaml"))
ensure_directories(_config, PROJECT_ROOT)

SURVEY_CACHE_PATH = PROJECT_ROOT / "temp" / "survey_cache.json"

_sp_cache: dict[str, SharePointClient] = {}


# ── SharePoint client ─────────────────────────────────────────────────

def _make_sp_client(site_url: str, root_folder: str, folder_sharing_url: str = "") -> SharePointClient:
    cache_key = f"{site_url}|{root_folder}|{folder_sharing_url}"
    if cache_key in _sp_cache:
        return _sp_cache[cache_key]
    sp_cfg = dict(_config.get("sharepoint", {}))
    configured_url = sp_cfg.get("site_url", "").rstrip("/")
    requested_url  = (site_url or "").rstrip("/")
    if requested_url and requested_url != configured_url:
        sp_cfg["site_url"] = site_url
        sp_cfg.pop("drive_id", None)
        # folder_sharing_url이 UI에서 제공된 경우 사용, 아니면 제거
        if folder_sharing_url:
            sp_cfg["folder_sharing_url"] = folder_sharing_url
        else:
            sp_cfg.pop("folder_sharing_url", None)
    elif requested_url:
        sp_cfg["site_url"] = site_url
        if folder_sharing_url:
            sp_cfg["folder_sharing_url"] = folder_sharing_url
    if root_folder is not None:
        sp_cfg["root_folder"] = root_folder
    token_cache = resolve_project_path(
        PROJECT_ROOT, sp_cfg.get("token_cache_path", "./temp/sp_token_cache.json")
    )
    client = SharePointClient(sp_cfg, token_cache_path=token_cache)
    client.authenticate()
    _sp_cache[cache_key] = client
    return client


def _get_sp_client(site_url: str = "", root_folder: str = "", folder_sharing_url: str = "") -> SharePointClient | None:
    cfg = _config.get("sharepoint", {})
    url    = (site_url or cfg.get("site_url", "")).rstrip("/")
    folder = root_folder or cfg.get("root_folder", "")
    fsu    = folder_sharing_url or cfg.get("folder_sharing_url", "")
    return _sp_cache.get(f"{url}|{folder}|{fsu}")


# ── Analysis session ──────────────────────────────────────────────────

@dataclass
class AnalysisSession:
    status: str = "idle"      # idle | scanning | running | complete | error | cancelled
    total: int = 0
    processed: int = 0
    cache_hits: int = 0
    manual_review: int = 0
    errors: int = 0
    start_time: float = 0.0
    records: list[dict] = field(default_factory=list)
    all_analysis_records: list = field(default_factory=list)
    review_path: str = ""
    last_error: str = ""
    site_url: str = ""
    root_folder: str = ""
    cancelled: bool = False


_session = AnalysisSession()
_session_lock = threading.Lock()
_analysis_subs: list[asyncio.Queue] = []
_main_loop: asyncio.AbstractEventLoop | None = None


def _broadcast(event: dict) -> None:
    if _main_loop is None:
        return
    data = json.dumps(event, ensure_ascii=False)
    def _put():
        for q in list(_analysis_subs):
            try:
                q.put_nowait(data)
            except Exception:
                pass
    _main_loop.call_soon_threadsafe(_put)


def _record_to_dict(ar: Any) -> dict:
    r = ar.file_record
    n = ar.naming
    return {
        "seq": r.seq,
        "original_file_name": r.original_file_name,
        "original_full_path": r.original_full_path,
        "original_dir_path": r.original_dir_path,
        "relative_path": r.relative_path_from_root,
        "sharepoint_item_id": r.sharepoint_item_id or "",
        "sharepoint_web_url": r.sharepoint_web_url or "",
        "file_extension": r.file_extension,
        "file_size": r.file_size,
        "suggested_file_name": n.suggested_file_name or r.original_file_name,
        "suggested_full_path": n.suggested_full_path or "",
        "doc_type": n.extracted_doc_type or "",
        "summary": n.extracted_summary or "",
        "confidence": round(n.confidence, 3),
        "rename_status": n.rename_status or "",
        "needs_manual_review": n.needs_manual_review,
        "conflict_detected": n.conflict_detected,
        "reason": n.reason or "",
    }


def _records_from_survey_cache(file_index: dict, sp_client: "SharePointClient", config: dict) -> list:
    """survey_cache file_index로 FileRecord 생성 — SharePoint 재스캔 없음."""
    from models import FileRecord
    supported_exts = {e.lower() for e in config.get("supported_extensions", [])}
    exclude_exts   = {e.lower() for e in config.get("exclude_extensions", [])}
    root_folder = (sp_client.root_folder or "").strip("/")

    records = []
    for item_id, info in file_index.items():
        name        = info["name"]
        ext         = info.get("ext", Path(name).suffix.lower())
        folder_abs  = info.get("folder_path", "/")
        supported   = ext in supported_exts and ext not in exclude_exts and not name.startswith("~$")
        full_path   = f"{folder_abs.rstrip('/')}/{name}"

        # relative_path_from_root: root_folder 아래 경로
        rel = f"{folder_abs.lstrip('/')}/{name}".lstrip("/")
        if root_folder:
            prefix = root_folder + "/"
            if rel.startswith(prefix):
                rel = rel[len(prefix):]

        records.append(FileRecord(
            seq=0,
            root_path=root_folder or "/",
            original_full_path=full_path,
            original_dir_path=folder_abs,
            original_file_name=name,
            file_extension=ext,
            file_size=info.get("size", 0),
            last_modified_time=info.get("modified", ""),
            relative_path_from_root=rel,
            supported=supported,
            sharepoint_item_id=item_id,
            sharepoint_web_url=info.get("web_url", ""),
        ))

    records.sort(key=lambda r: (r.original_dir_path, r.original_file_name))
    for i, r in enumerate(records, 1):
        r.seq = i
    return records


def _perf_config(fast: bool = False, max_files: int = 0) -> dict:
    perf = _config.get("performance", {})
    cpu = os.cpu_count() or 4
    dw = max(1, min(6, cpu))
    return {
        "workers":           int(perf.get("workers",           dw)),
        "ocr_workers":       int(perf.get("ocr_workers",       max(1, min(2, dw)))),
        "llm_workers":       int(perf.get("llm_workers",       max(1, min(3, dw)))),
        "extract_max_chars": int(perf.get("extract_max_chars", 15000)),
        "llm_excerpt_chars": int(perf.get("llm_excerpt_chars", 5000)),
        "max_files":         max_files or int(perf.get("max_files", 0)),
        "fast":              fast,
        "fast_disables_ocr": bool(perf.get("fast_disables_ocr", True)),
        "fast_disables_llm": bool(perf.get("fast_disables_llm", True)),
    }


def _run_analysis(site_url: str, root_folder: str, max_files: int, fast: bool,
                  clear_cache: bool = False, folder_paths: list[str] | None = None,
                  folder_sharing_url: str = "") -> None:
    global _session

    with _session_lock:
        _session = AnalysisSession(
            status="scanning",
            start_time=_time.monotonic(),
            site_url=site_url,
            root_folder=root_folder,
        )

    _broadcast({"type": "scanning", "message": "파일 목록 조회 중..."})

    try:
        sp_client = _get_sp_client(site_url, root_folder, folder_sharing_url)
        if sp_client is None:
            sp_client = _make_sp_client(site_url, root_folder, folder_sharing_url)

        if sp_client:
            # 현황조회 캐시가 있으면 재스캔 없이 file_index를 바로 사용 (수 초 → 즉시)
            _survey_cache = load_survey_cache(SURVEY_CACHE_PATH)
            _file_index   = _survey_cache.get("file_index") if _survey_cache else None
            if _file_index:
                scanned = _records_from_survey_cache(_file_index, sp_client, _config)
                print(f"[scan] 캐시 사용: {len(scanned)}개 파일 (SharePoint 재스캔 생략)")
            else:
                scanned = scan_sharepoint_files(sp_client, _config)
                print(f"[scan] SharePoint 전체 스캔: {len(scanned)}개 파일")
        else:
            scanned = scan_files(Path(_config["input_root"]), _config)

        # 폴더 범위 필터링
        if folder_paths:
            def _in_scope(r: Any) -> bool:
                rp = r.relative_path_from_root.replace('\\', '/')
                return any(
                    rp.startswith(fp.rstrip('/') + '/') or rp == fp
                    for fp in folder_paths
                )
            scanned = [r for r in scanned if _in_scope(r)]

        if max_files > 0:
            scanned = scanned[:max_files]

        total = len(scanned)
        with _session_lock:
            _session.status = "running"
            _session.total = total

        _broadcast({"type": "start", "total": total, "folder_paths": folder_paths or []})

        # Build runtime config
        perf = _perf_config(fast=fast, max_files=max_files)
        runtime_cfg = dict(_config)
        runtime_cfg["ocr"]  = dict(_config.get("ocr", {}))
        runtime_cfg["llm"]  = dict(_config.get("llm", {}))
        if fast and perf["fast_disables_ocr"]:
            runtime_cfg["ocr"]["enabled"] = False
        if fast and perf["fast_disables_llm"]:
            runtime_cfg["llm"]["enabled"] = False

        temp_dir  = resolve_project_path(PROJECT_ROOT, _config["temp"]["dir"])
        llm = OllamaClient(runtime_cfg, PROJECT_ROOT) if runtime_cfg["llm"].get("enabled", True) else None

        cache_path = _cache_file_path(PROJECT_ROOT)
        if clear_cache and cache_path.exists():
            cache_path.unlink(missing_ok=True)
        cache_data = _load_cache(cache_path)
        cache_entries: dict = dict(cache_data.get("entries", {}))

        all_records = []
        uncached    = []

        for record in scanned:
            with _session_lock:
                if _session.cancelled:
                    break
            ck = _build_cache_key(record, runtime_cfg, perf)
            cached = cache_entries.get(ck)
            if isinstance(cached, dict) and isinstance(cached.get("extraction"), dict):
                try:
                    from models import ExtractionResult
                    from main import _build_analysis_result
                    ext = ExtractionResult(**cached["extraction"])
                    ar  = _build_analysis_result(record, ext, cached.get("llm_result"), runtime_cfg)
                    all_records.append(ar)
                    rec = _record_to_dict(ar)
                    with _session_lock:
                        _session.records.append(rec)
                        _session.processed += 1
                        _session.cache_hits += 1
                        if ar.naming.needs_manual_review:
                            _session.manual_review += 1
                    elapsed = _time.monotonic() - _session.start_time
                    _broadcast({
                        "type": "record",
                        "processed": _session.processed,
                        "total": total,
                        "elapsed": round(elapsed, 1),
                        "cache_hit": True,
                        "record": rec,
                    })
                    continue
                except Exception:
                    pass
            uncached.append((record, ck))

        ocr_sem = BoundedSemaphore(perf["ocr_workers"])
        llm_sem = BoundedSemaphore(perf["llm_workers"])
        sp_sem  = BoundedSemaphore(max(1, perf["workers"])) if sp_client else None
        active_files: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=perf["workers"],
                                thread_name_prefix="analysis") as executor:
            futures = {
                executor.submit(
                    _analyze_single_record,
                    rec, runtime_cfg, temp_dir, llm,
                    bool(runtime_cfg["llm"].get("enabled", True)),
                    perf["llm_excerpt_chars"], perf["extract_max_chars"],
                    ck, ocr_sem, llm_sem, sp_client, sp_sem, active_files,
                ): rec
                for rec, ck in uncached
            }

            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                with _session_lock:
                    if _session.cancelled:
                        for f in pending:
                            f.cancel()
                        pending.clear()
                        break
                for future in done:
                    record = futures[future]
                    try:
                        ar, ck, payload = future.result()
                        cache_entries[ck] = payload
                        all_records.append(ar)
                        rec = _record_to_dict(ar)
                        with _session_lock:
                            _session.records.append(rec)
                            _session.processed += 1
                            if ar.naming.needs_manual_review:
                                _session.manual_review += 1
                    except Exception as exc:
                        with _session_lock:
                            _session.processed += 1
                            _session.errors += 1
                        rec = {
                            "seq": record.seq,
                            "original_file_name": record.original_file_name,
                            "original_full_path": record.original_full_path,
                            "original_dir_path": record.original_dir_path,
                            "relative_path": record.relative_path_from_root,
                            "sharepoint_item_id": record.sharepoint_item_id or "",
                            "sharepoint_web_url": record.sharepoint_web_url or "",
                            "file_extension": record.file_extension,
                            "file_size": record.file_size,
                            "suggested_file_name": record.original_file_name,
                            "suggested_full_path": "",
                            "doc_type": "", "summary": "",
                            "confidence": 0.0, "rename_status": "error",
                            "needs_manual_review": True, "conflict_detected": False,
                            "reason": f"분석 처리 오류: {exc}",
                        }
                        with _session_lock:
                            _session.records.append(rec)

                    elapsed = _time.monotonic() - _session.start_time
                    _broadcast({
                        "type": "record",
                        "processed": _session.processed,
                        "total": total,
                        "elapsed": round(elapsed, 1),
                        "cache_hit": False,
                        "active": list(active_files.values()),
                        "record": rec,
                    })

        # 취소된 경우 중간 결과만 저장하고 종료
        with _session_lock:
            is_cancelled = _session.cancelled
        if is_cancelled:
            _save_cache(cache_path, {"schema_version": CACHE_SCHEMA_VERSION, "entries": cache_entries})
            elapsed = _time.monotonic() - _session.start_time
            with _session_lock:
                _session.status = "cancelled"
            _broadcast({
                "type": "cancelled",
                "processed": _session.processed,
                "total": total,
                "elapsed": round(elapsed, 1),
            })
            return

        # Finalize
        _save_cache(cache_path, {"schema_version": CACHE_SCHEMA_VERSION, "entries": cache_entries})
        all_records.sort(key=lambda x: x.file_record.seq)
        mark_conflicts(all_records)

        # Replace session records with conflict-resolved, seq-sorted list
        with _session_lock:
            _session.records = [_record_to_dict(ar) for ar in all_records]
            _session.all_analysis_records = list(all_records)

        # Write Excel
        review_dir = resolve_project_path(PROJECT_ROOT, _config["review"]["output_dir"])
        try:
            wb_path = write_review_workbook(all_records, review_dir)
            with _session_lock:
                _session.review_path = str(wb_path)
        except Exception:
            pass

        elapsed = _time.monotonic() - _session.start_time
        with _session_lock:
            _session.status = "complete"

        _broadcast({
            "type": "complete",
            "processed": _session.processed,
            "total": total,
            "manual_review": _session.manual_review,
            "errors": _session.errors,
            "cache_hits": _session.cache_hits,
            "elapsed": round(elapsed, 1),
            "review_path": _session.review_path,
        })

    except Exception as exc:
        with _session_lock:
            _session.status = "error"
            _session.last_error = str(exc)
        _broadcast({"type": "error", "message": str(exc)})


# ── App lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    sp_cfg = _config.get("sharepoint", {})
    default_url    = sp_cfg.get("site_url", "")
    default_folder = sp_cfg.get("root_folder", "")
    if default_url:
        try:
            await _main_loop.run_in_executor(None, lambda: _make_sp_client(default_url, default_folder))
            print("[server] SharePoint 인증 완료 (토큰 캐시 활성)")
        except Exception as exc:
            print(f"[server] 사전 인증 실패 (첫 조회 시 재시도): {exc}")
    yield
    # 서버 종료 시 진행 중인 분석 스레드에 중단 신호 전달 → 프로세스가 즉시 종료될 수 있도록
    with _session_lock:
        if _session.status in ("scanning", "running"):
            _session.cancelled = True
    print("[server] 분석 스레드 중단 신호 전달 완료")


app = FastAPI(title="법무 문서 DB 파일명 자동화", lifespan=lifespan)


# ── Phase 1: Survey ───────────────────────────────────────────────────

@app.get("/api/config")
async def get_default_config():
    sp_cfg = _config.get("sharepoint", {})
    return {
        "site_url": sp_cfg.get("site_url", ""),
        "root_folder": sp_cfg.get("root_folder", ""),
        "folder_sharing_url": sp_cfg.get("folder_sharing_url", ""),
    }


@app.get("/api/survey/stream")
async def survey_stream(site_url: str = Query(default=""), root_folder: str = Query(default=""), folder_sharing_url: str = Query(default="")):
    loop = asyncio.get_event_loop()
    q: asyncio.Queue[str | None] = asyncio.Queue()
    counter = {"files": 0, "folders": 0, "last_sent": 0}

    def send(payload: dict) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def progress_cb(n: int) -> None:
        counter["files"] += n
        if counter["files"] - counter["last_sent"] >= 30:
            counter["last_sent"] = counter["files"]
            send({"type": "progress", "files": counter["files"], "folders": counter["folders"]})

    def folder_cb(path: str) -> None:
        counter["folders"] += 1
        send({"type": "status", "stage": "scan",
              "message": path, "files": counter["files"], "folders": counter["folders"]})

    def _work() -> None:
        try:
            # 전체 재조회 시작 전 기존 캐시 삭제 — 새 결과만 캐시로 저장
            if SURVEY_CACHE_PATH.exists():
                SURVEY_CACHE_PATH.unlink()
                print("[survey] 기존 캐시 삭제 완료 → 전체 재스캔 시작")
            send({"type": "status", "stage": "auth", "message": "SharePoint 인증 중..."})
            client = _make_sp_client(site_url, root_folder, folder_sharing_url)
            send({"type": "status", "stage": "drive", "message": "드라이브 연결 확인 중..."})
            tree, delta_link, file_index = client.build_folder_tree_with_delta(
                progress_callback=progress_cb, folder_callback=folder_cb
            )
            send({"type": "progress", "files": counter["files"], "folders": counter["folders"]})
            send({"type": "status", "stage": "build", "message": "결과 집계 중..."})
            data = build_survey_data(tree, client.site_url)
            try:
                save_survey_cache(SURVEY_CACHE_PATH, data, delta_link, file_index,
                                  folder_sharing_url=folder_sharing_url)
                print(f"[survey] 캐시 저장 완료 | delta_link={'<EMPTY>' if not delta_link else delta_link[:80]+'...'}")
            except Exception as _ce:
                print(f"[survey] 캐시 저장 실패: {_ce}")
            from datetime import datetime as _dt2
            send({"type": "complete", "data": data, "scanned_at": _dt2.now().isoformat()})
        except Exception as exc:
            send({"type": "error", "message": str(exc)})
        loop.call_soon_threadsafe(q.put_nowait, None)

    async def run_worker():
        await loop.run_in_executor(None, _work)

    asyncio.create_task(run_worker())

    async def generate():
        try:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {item}\n\n"
                try:
                    if json.loads(item).get("type") in ("complete", "error"):
                        break
                except Exception:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/survey/cache")
async def get_survey_cache():
    cache = load_survey_cache(SURVEY_CACHE_PATH)
    if not cache:
        return {"available": False}
    return {
        "available": True,
        "scanned_at": cache["scanned_at"],
        "site_url": cache["site_url"],
        "survey_data": cache["survey_data"],
    }


@app.get("/api/survey/delta/stream")
async def survey_delta_stream(site_url: str = Query(default=""), root_folder: str = Query(default=""), folder_sharing_url: str = Query(default="")):
    from datetime import datetime as _dt
    loop = asyncio.get_event_loop()
    q: asyncio.Queue[str | None] = asyncio.Queue()

    def send(payload: dict) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def _work() -> None:
        try:
            cache = load_survey_cache(SURVEY_CACHE_PATH)
            if not cache:
                send({"type": "error", "message": "캐시가 없습니다. 현황 조회를 먼저 실행하세요."})
                loop.call_soon_threadsafe(q.put_nowait, None)
                return
            delta_link = cache.get("delta_link", "")
            if not delta_link:
                send({"type": "error", "message": "델타 링크가 없습니다. 현황 조회를 다시 실행하세요."})
                loop.call_soon_threadsafe(q.put_nowait, None)
                return

            send({"type": "status", "message": "변경 내역 확인 중..."})

            _url = site_url or cache.get("site_url", "")
            # folder_sharing_url: UI 값 우선, 없으면 현황조회 시 저장된 캐시 값 사용
            _fsu = folder_sharing_url or cache.get("folder_sharing_url", "")
            client = _get_sp_client(_url, root_folder, _fsu)
            if not client:
                client = _make_sp_client(_url, root_folder, _fsu)

            try:
                delta_items, new_delta_link = client.get_delta(delta_link)
            except _requests.HTTPError as _he:
                if _he.response is not None and _he.response.status_code == 410:
                    # 델타 토큰 만료 → 프론트에 전체 재조회 전환 요청
                    send({"type": "resync_required"})
                    loop.call_soon_threadsafe(q.put_nowait, None)
                    return
                raise

            old_index: dict = cache.get("file_index", {})
            changes = compute_delta_changes(old_index, delta_items)

            # Apply changes to file_index
            new_index = dict(old_index)
            for item in changes["deleted"]:
                new_index.pop(item["id"], None)
            for item in changes["added"]:
                iid = item["id"]
                new_index[iid] = {k: v for k, v in item.items() if k != "id"}
            for item in changes["modified"]:
                iid = item["id"]
                new_index[iid] = {
                    "name": item["new_name"],
                    "folder_path": item["folder_path"],
                    "size": item["size"],
                    "modified": item["modified"],
                    "ext": item["ext"],
                }

            # Recalculate stats
            updated_survey = dict(cache["survey_data"])
            updated_survey["total_files"] = len(new_index)
            updated_survey["supported_files"] = sum(
                1 for f in new_index.values() if f.get("ext") in SUPPORTED_EXTS_SET
            )
            updated_survey["unsupported_files"] = (
                updated_survey["total_files"] - updated_survey["supported_files"]
            )

            save_survey_cache(SURVEY_CACHE_PATH, updated_survey, new_delta_link or delta_link, new_index,
                              folder_sharing_url=_fsu)

            # Per-folder change aggregation
            folder_changes: dict[str, dict] = {}
            for _item in changes["added"]:
                _fp = _item.get("folder_path", "")
                if _fp:
                    _fc = folder_changes.setdefault(_fp, {"added": 0, "deleted": 0, "modified": 0})
                    _fc["added"] += 1
            for _item in changes["deleted"]:
                _fp = _item.get("folder_path", "")
                if _fp:
                    _fc = folder_changes.setdefault(_fp, {"added": 0, "deleted": 0, "modified": 0})
                    _fc["deleted"] += 1
            for _item in changes["modified"]:
                _fp = _item.get("folder_path", "")
                if _fp:
                    _fc = folder_changes.setdefault(_fp, {"added": 0, "deleted": 0, "modified": 0})
                    _fc["modified"] += 1

            send({
                "type": "complete",
                "scanned_at": _dt.now().isoformat(),
                "changes": {
                    "added": len(changes["added"]),
                    "deleted": len(changes["deleted"]),
                    "modified": len(changes["modified"]),
                    "by_folder": folder_changes,
                },
                "survey_data": updated_survey,
            })
        except Exception as exc:
            send({"type": "error", "message": str(exc)})
        loop.call_soon_threadsafe(q.put_nowait, None)

    async def run_worker():
        await loop.run_in_executor(None, _work)

    asyncio.create_task(run_worker())

    async def generate():
        try:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {item}\n\n"
                try:
                    if json.loads(item).get("type") in ("complete", "error"):
                        break
                except Exception:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Phase 2: Analysis ─────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    site_url: str = ""
    root_folder: str = ""
    folder_sharing_url: str = ""
    max_files: int = 0
    fast: bool = False
    clear_cache: bool = False
    folder_paths: list[str] = []  # 빈 리스트 = 전체 처리


@app.post("/api/analyze/start")
async def analyze_start(req: AnalyzeRequest):
    print(f"[analyze] folder_paths={req.folder_paths!r}")
    with _session_lock:
        if _session.status in ("scanning", "running"):
            return {"error": "이미 분석이 진행 중입니다.", "status": _session.status}
    loop = asyncio.get_running_loop()

    async def _bg():
        await loop.run_in_executor(
            None, lambda: _run_analysis(
                req.site_url, req.root_folder, req.max_files, req.fast,
                req.clear_cache, req.folder_paths or [], req.folder_sharing_url
            )
        )

    asyncio.create_task(_bg())
    return {"status": "started"}


@app.post("/api/analyze/stop")
async def analyze_stop():
    with _session_lock:
        if _session.status in ("scanning", "running"):
            _session.cancelled = True
            return {"status": "stopping"}
        return {"status": _session.status}


@app.get("/api/analyze/stream")
async def analyze_stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _analysis_subs.append(q)
    with _session_lock:
        initial = {
            "type": "sync",
            "status": _session.status,
            "total": _session.total,
            "processed": _session.processed,
            "cache_hits": _session.cache_hits,
            "manual_review": _session.manual_review,
            "errors": _session.errors,
            "records": list(_session.records),
        }

    async def generate():
        try:
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {item}\n\n"
                try:
                    if json.loads(item).get("type") in ("complete", "error"):
                        break
                except Exception:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            if q in _analysis_subs:
                _analysis_subs.remove(q)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/analyze/results")
async def analyze_results():
    with _session_lock:
        return {
            "status": _session.status,
            "total": _session.total,
            "processed": _session.processed,
            "manual_review": _session.manual_review,
            "errors": _session.errors,
            "review_path": _session.review_path,
            "records": list(_session.records),
        }


@app.get("/api/analyze/download/excel")
async def analyze_download_excel():
    with _session_lock:
        path   = _session.review_path
        status = _session.status
    if status != "complete" or not path:
        return {"error": "분석이 완료된 결과가 없습니다."}
    file_path = Path(path)
    if not file_path.exists():
        return {"error": f"파일을 찾을 수 없습니다: {file_path.name}"}
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=file_path.name,
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )


@app.get("/api/analyze/status")
async def analyze_status():
    with _session_lock:
        elapsed = _time.monotonic() - _session.start_time if _session.start_time else 0
        return {
            "status": _session.status,
            "total": _session.total,
            "processed": _session.processed,
            "cache_hits": _session.cache_hits,
            "manual_review": _session.manual_review,
            "errors": _session.errors,
            "elapsed": round(elapsed, 1),
        }


# ── Phase 3: Rename / Rollback ────────────────────────────────────────

class RenameItem(BaseModel):
    original_full_path: str
    suggested_full_path: str
    sharepoint_item_id: str = ""
    manually_edited: bool = False


class RenameRequest(BaseModel):
    items: list[RenameItem]
    site_url: str = ""
    root_folder: str = ""
    folder_sharing_url: str = ""


@app.post("/api/rename")
async def rename_files(req: RenameRequest):
    if not req.items:
        return {"error": "선택된 항목이 없습니다."}
    sp_client = _get_sp_client(req.site_url, req.root_folder, req.folder_sharing_url)
    if sp_client is None and req.site_url:
        sp_client = _make_sp_client(req.site_url, req.root_folder, req.folder_sharing_url)
    review_rows = [
        {"approval": "Y", "original_full_path": it.original_full_path,
         "suggested_full_path": it.suggested_full_path, "sharepoint_item_id": it.sharepoint_item_id}
        for it in req.items
    ]
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: execute_rename(
        review_rows=review_rows,
        config=_config,
        logs_dir=resolve_project_path(PROJECT_ROOT, _config["logs"]["output_dir"]),
        rollback_dir=resolve_project_path(PROJECT_ROOT, _config["rollback"]["output_dir"]),
        sp_client=sp_client,
        site_url=req.site_url,
    ))

    # 수동수정 항목이 있으면 AnalysisRecord 갱신 후 Excel 재생성
    manually_edited_paths = {it.original_full_path for it in req.items if it.manually_edited}
    if manually_edited_paths:
        path_to_suggested = {it.original_full_path: it.suggested_full_path for it in req.items}
        with _session_lock:
            analysis_records = list(_session.all_analysis_records)
            review_path = _session.review_path
        if analysis_records and review_path:
            for ar in analysis_records:
                orig = ar.file_record.original_full_path
                if orig in manually_edited_paths:
                    new_sfp = path_to_suggested[orig]
                    ar.naming.suggested_full_path = new_sfp
                    ar.naming.suggested_file_name = Path(new_sfp).name
                    ar.naming.manually_edited = True
            try:
                new_wb = write_review_workbook(analysis_records, Path(review_path).parent)
                old = Path(review_path)
                with _session_lock:
                    _session.review_path = str(new_wb)
                if old.exists() and old != new_wb:
                    old.unlink(missing_ok=True)
            except Exception as exc:
                print(f"[rename] Excel 재생성 실패: {exc}")

    return result


@app.get("/api/rollback/list")
async def rollback_list():
    rollback_dir = resolve_project_path(PROJECT_ROOT, _config["rollback"]["output_dir"])
    files = sorted(rollback_dir.glob("rollback_mapping_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        entry = {"filename": f.name, "path": str(f), "mtime": f.stat().st_mtime, "site_url": ""}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entry["site_url"] = data.get("site_url", "")
        except Exception:
            pass
        result.append(entry)
    return result


class RollbackDeleteRequest(BaseModel):
    rollback_file: str


@app.delete("/api/rollback/delete")
async def rollback_delete(req: RollbackDeleteRequest):
    f = Path(req.rollback_file)
    rollback_dir = resolve_project_path(PROJECT_ROOT, _config["rollback"]["output_dir"])
    try:
        f.resolve().relative_to(rollback_dir.resolve())
    except ValueError:
        return {"error": "허용되지 않은 경로입니다."}
    if not f.exists():
        return {"error": f"파일을 찾을 수 없습니다: {f.name}"}
    f.unlink()
    return {"ok": True, "filename": f.name}


class RollbackRequest(BaseModel):
    rollback_file: str
    site_url: str = ""
    root_folder: str = ""
    folder_sharing_url: str = ""
    folder_paths: list[str] = []  # 빈 리스트 = 전체 복원


class RollbackPreviewRequest(BaseModel):
    rollback_file: str
    folder_paths: list[str] = []


@app.post("/api/rollback/preview")
async def rollback_preview(req: RollbackPreviewRequest):
    f = Path(req.rollback_file)
    if not f.exists():
        return {"error": f"파일을 찾을 수 없습니다: {f.name}"}
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: preview_rollback(f, req.folder_paths or [])
    )
    return result


@app.post("/api/rollback")
async def rollback_files(req: RollbackRequest):
    with _session_lock:
        if _session.status in ("scanning", "running"):
            return {"error": "파일명 분석이 진행 중입니다. 분석 완료 또는 중단 후 롤백을 실행하세요."}
    sp_client = _get_sp_client(req.site_url, req.root_folder, req.folder_sharing_url)
    if sp_client is None:
        cfg = _config.get("sharepoint", {})
        _url = req.site_url or cfg.get("site_url", "")
        _folder = req.root_folder or cfg.get("root_folder", "")
        _fsu = req.folder_sharing_url or cfg.get("folder_sharing_url", "")
        if _url:
            sp_client = _make_sp_client(_url, _folder, _fsu)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: execute_rollback(
            Path(req.rollback_file),
            sp_client=sp_client,
            folder_paths=req.folder_paths or [],
        )
    )
    return result


# ── SharePoint diagnostics ────────────────────────────────────────────

class TestSharingUrlRequest(BaseModel):
    site_url: str = ""
    root_folder: str = ""
    folder_sharing_url: str


@app.post("/api/sp/test-sharing-url")
async def test_sharing_url(req: TestSharingUrlRequest):
    """사이트 연결(드라이브 ID 해석)을 테스트합니다. folder_sharing_url은 선택사항."""
    if not req.site_url and not req.folder_sharing_url:
        return {"ok": False, "error": "사이트 URL 또는 폴더 공유 URL을 입력하세요."}
    loop = asyncio.get_event_loop()

    def _work():
        try:
            client = _get_sp_client(req.site_url, req.root_folder, req.folder_sharing_url)
            if client is None:
                client = _make_sp_client(req.site_url, req.root_folder, req.folder_sharing_url)
            # _resolve_drive() 호출 → 공유 URL 또는 SP REST API fallback 자동 실행
            drive_id = client._resolve_drive()
            return {
                "ok": True,
                "drive_id": drive_id,
                "drive_id_short": drive_id[:16] + "..." if drive_id else "",
                "method": "sharing_url" if client._sharing_url_root_id else "sp_rest_api",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    result = await loop.run_in_executor(None, _work)
    return result


# ── Static serving (must be last) ────────────────────────────────────

_frontend_dir = PROJECT_ROOT / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
