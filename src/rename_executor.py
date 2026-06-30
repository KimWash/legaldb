from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING
import csv
import json
import time

if TYPE_CHECKING:
    from sharepoint_client import SharePointClient


INVALID_CHARS = set('\\/:*?"<>|')


def _is_valid_filename(name: str) -> bool:
    return bool(name) and not any(char in INVALID_CHARS for char in name)


def execute_rename(
    review_rows: list[dict],
    config: dict,
    logs_dir: Path,
    rollback_dir: Path,
    sp_client: "SharePointClient | None" = None,
    site_url: str = "",
    progress_callback = None,
) -> dict:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = logs_dir / f"rename_log_{timestamp}.jsonl"
    csv_path = logs_dir / f"rename_result_{timestamp}.csv"
    rollback_path = rollback_dir / f"rollback_mapping_{timestamp}.json"

    results: list[dict] = []
    rollback_items: list[dict] = []
    path_limit = int(config["rename"].get("path_length_limit", 240))

    # SharePoint 파일인데 --source sharepoint 없이 실행하는 케이스 조기 감지
    approved_rows = [r for r in review_rows if str(r.get("approval") or "").strip().upper() == "Y"]
    if sp_client is None and approved_rows:
        first_sp_id = str(approved_rows[0].get("sharepoint_item_id") or "").strip()
        if first_sp_id:
            raise SystemExit(
                "[rename] SharePoint 파일이 감지되었으나 --source sharepoint 옵션이 없습니다.\n"
                "  올바른 명령: python src/main.py --mode rename --source sharepoint --review-file <파일>"
            )

    # ── 사전 검증: 로컬 파일 rows 처리 + SP rows 분리 ────────────────
    sp_pending: list[dict] = []   # SharePoint rename 대기 row 목록

    for row in review_rows:
        approval = str(row.get("approval") or "").strip().upper()
        if approval != "Y":
            continue

        sp_item_id = str(row.get("sharepoint_item_id") or "").strip()
        original_path = Path(str(row.get("original_full_path") or ""))
        target_path = Path(str(row.get("suggested_full_path") or ""))
        new_name = target_path.name

        # 파일명 유효성 검증 (SP/로컬 공통)
        if not _is_valid_filename(new_name):
            results.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "original_full_path": str(original_path),
                "new_full_path": str(target_path),
                "sharepoint_item_id": sp_item_id,
                "status": "invalid_filename",
                "reason": "금지 문자가 포함된 파일명입니다.",
            })
            if progress_callback:
                try:
                    progress_callback({
                        "original_full_path": str(original_path),
                        "new_full_path": str(target_path),
                        "status": "invalid_filename",
                        "reason": "금지 문자가 포함된 파일명입니다.",
                    })
                except Exception as exc:
                    print(f"[rename_executor] Progress callback error: {exc}")
            continue

        if sp_item_id and sp_client is not None:
            # SharePoint: 배치 처리 대기열에 추가
            sp_pending.append(row)
        else:
            # Local filesystem rename
            status = "pending"
            reason = ""
            if not original_path.exists():
                status = "source_missing"
                reason = "원본 파일이 존재하지 않습니다."
            elif len(str(target_path)) > path_limit:
                status = "path_too_long"
                reason = "경로 길이 제한을 초과합니다."
            elif target_path.exists() and original_path.resolve() != target_path.resolve():
                status = "target_exists"
                reason = "동일한 대상 파일명이 이미 존재합니다."
            else:
                try:
                    original_path.rename(target_path)
                    status = "success"
                    reason = "파일명 변경 완료"
                    rollback_items.append({
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "original_full_path": str(original_path),
                        "new_full_path": str(target_path),
                        "original_file_name": original_path.name,
                        "new_file_name": target_path.name,
                        "sharepoint_item_id": "",
                    })
                except Exception as exc:
                    status = "rename_failed"
                    reason = str(exc)

            results.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "original_full_path": str(original_path),
                "new_full_path": str(target_path),
                "sharepoint_item_id": sp_item_id,
                "status": status,
                "reason": reason,
            })
            if progress_callback:
                try:
                    progress_callback({
                        "original_full_path": str(original_path),
                        "new_full_path": str(target_path),
                        "status": status,
                        "reason": reason,
                    })
                except Exception as exc:
                    print(f"[rename_executor] Progress callback error: {exc}")

    # ── SharePoint 배치 rename ────────────────────────────────────────
    # 공식 문서: https://learn.microsoft.com/en-us/graph/json-batching
    # - 최대 20개/배치
    # - 배치 전체 HTTP 응답은 200, 개별 status로 성공/실패 판단
    # - 429 발생 시 retry-after 헤더 값만큼 대기 후 재시도
    BATCH_SIZE = 20

    if sp_pending and sp_client is not None:
        # 20개씩 청크로 분할
        for chunk_start in range(0, len(sp_pending), BATCH_SIZE):
            chunk = sp_pending[chunk_start: chunk_start + BATCH_SIZE]

            batch_items = [
                {
                    "item_id": str(row.get("sharepoint_item_id") or "").strip(),
                    "new_name": Path(str(row.get("suggested_full_path") or "")).name,
                }
                for row in chunk
            ]

            # 429 대응: 최대 3회 재시도
            max_retries = 3
            attempt = 0
            batch_results: list[dict] = []

            while attempt < max_retries:
                attempt += 1
                try:
                    batch_results = sp_client.batch_rename_items(batch_items)
                except Exception as exc:
                    # $batch POST 자체가 실패한 경우 (네트워크 오류 등)
                    print(f"[rename_executor] $batch 요청 실패 (시도 {attempt}/{max_retries}): {exc}")
                    if attempt >= max_retries:
                        # 청크 전체를 실패 처리
                        for row in chunk:
                            original_path = Path(str(row.get("original_full_path") or ""))
                            target_path = Path(str(row.get("suggested_full_path") or ""))
                            sp_item_id = str(row.get("sharepoint_item_id") or "").strip()
                            results.append({
                                "timestamp": datetime.now().isoformat(timespec="seconds"),
                                "original_full_path": str(original_path),
                                "new_full_path": str(target_path),
                                "sharepoint_item_id": sp_item_id,
                                "status": "rename_failed",
                                "reason": f"$batch 요청 오류: {exc}",
                            })
                            if progress_callback:
                                try:
                                    progress_callback({
                                        "original_full_path": str(original_path),
                                        "new_full_path": str(target_path),
                                        "status": "rename_failed",
                                        "reason": f"$batch 요청 오류: {exc}",
                                    })
                                except Exception as cb_exc:
                                    print(f"[rename_executor] Progress callback error: {cb_exc}")
                        break
                    time.sleep(5)
                    continue

                # 429 가 있으면 retry_after 최댓값만큼 대기 후 해당 항목만 재시도
                throttled = [r for r in batch_results if r["status"] == 429]
                if throttled:
                    wait_sec = max((r["retry_after"] or 10) for r in throttled)
                    print(f"[rename_executor] 429 Throttled — {wait_sec}초 대기 후 재시도 ({len(throttled)}건)")
                    time.sleep(wait_sec)
                    # 실패한 항목만 재배치 (성공한 항목은 보존)
                    ok_results = [r for r in batch_results if r["status"] != 429]
                    retry_items = [
                        {"item_id": r["item_id"], "new_name": r["new_name"]}
                        for r in throttled
                    ]
                    try:
                        retry_results = sp_client.batch_rename_items(retry_items)
                        batch_results = ok_results + retry_results
                    except Exception as retry_exc:
                        print(f"[rename_executor] 재시도 실패: {retry_exc}")
                        # 재시도 실패 항목도 error로 기록
                        for r in throttled:
                            r["ok"] = False
                            r["error"] = f"재시도 실패: {retry_exc}"
                        batch_results = ok_results + throttled
                # 429 없으면 재시도 불필요
                break

            # 배치 결과를 results에 반영
            for row, br in zip(chunk, batch_results):
                original_path = Path(str(row.get("original_full_path") or ""))
                target_path = Path(str(row.get("suggested_full_path") or ""))
                sp_item_id = str(row.get("sharepoint_item_id") or "").strip()

                if br.get("ok"):
                    status = "success"
                    reason = "파일명 변경 완료 (SharePoint Batch)"
                    rollback_items.append({
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "original_full_path": str(original_path),
                        "new_full_path": str(target_path),
                        "original_file_name": original_path.name,
                        "new_file_name": br.get("new_name", target_path.name),
                        "sharepoint_item_id": sp_item_id,
                    })
                else:
                    status = "rename_failed"
                    reason = br.get("error") or f"HTTP {br.get('status')}"

                results.append({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "original_full_path": str(original_path),
                    "new_full_path": str(target_path),
                    "sharepoint_item_id": sp_item_id,
                    "status": status,
                    "reason": reason,
                })
                if progress_callback:
                    try:
                        progress_callback({
                            "original_full_path": str(original_path),
                            "new_full_path": str(target_path),
                            "status": status,
                            "reason": reason,
                        })
                    except Exception as exc:
                        print(f"[rename_executor] Progress callback error: {exc}")

    with jsonl_path.open("w", encoding="utf-8") as file:
        for item in results:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["timestamp", "original_full_path", "new_full_path", "sharepoint_item_id", "status", "reason"],
        )
        writer.writeheader()
        writer.writerows(results)

    if rollback_items:
        rollback_envelope = {"site_url": site_url, "items": rollback_items}
        with rollback_path.open("w", encoding="utf-8") as file:
            json.dump(rollback_envelope, file, ensure_ascii=False, indent=2)
    else:
        rollback_path = None

    return {
        "log_jsonl": str(jsonl_path),
        "result_csv": str(csv_path),
        "rollback_file": str(rollback_path) if rollback_path else None,
        "processed_count": len(results),
        "success_count": sum(1 for item in results if item["status"] == "success"),
        "file_results": [
            {"original_full_path": r["original_full_path"], "status": r["status"]}
            for r in results
        ],
    }
