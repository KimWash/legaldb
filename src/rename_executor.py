from __future__ import annotations

from pathlib import Path
from datetime import datetime
import csv
import json


INVALID_CHARS = set('\\/:*?"<>|')


def _is_valid_filename(name: str) -> bool:
    return bool(name) and not any(char in INVALID_CHARS for char in name)


def execute_rename(review_rows: list[dict], config: dict, logs_dir: Path, rollback_dir: Path) -> dict:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = logs_dir / f"rename_log_{timestamp}.jsonl"
    csv_path = logs_dir / f"rename_result_{timestamp}.csv"
    rollback_path = rollback_dir / f"rollback_mapping_{timestamp}.json"

    results: list[dict] = []
    rollback_items: list[dict] = []
    path_limit = int(config["rename"].get("path_length_limit", 240))

    for row in review_rows:
        approval = str(row.get("approval") or "").strip().upper()
        if approval != "Y":
            continue

        original_path = Path(str(row.get("original_full_path") or ""))
        target_path = Path(str(row.get("suggested_full_path") or ""))
        status = "pending"
        reason = ""

        if not original_path.exists():
            status = "source_missing"
            reason = "원본 파일이 존재하지 않습니다."
        elif not _is_valid_filename(target_path.name):
            status = "invalid_filename"
            reason = "금지 문자가 포함된 파일명입니다."
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
                rollback_items.append(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "original_full_path": str(original_path),
                        "new_full_path": str(target_path),
                        "original_file_name": original_path.name,
                        "new_file_name": target_path.name,
                    }
                )
            except Exception as exc:
                status = "rename_failed"
                reason = str(exc)

        results.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "original_full_path": str(original_path),
                "new_full_path": str(target_path),
                "status": status,
                "reason": reason,
            }
        )

    with jsonl_path.open("w", encoding="utf-8") as file:
        for item in results:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["timestamp", "original_full_path", "new_full_path", "status", "reason"])
        writer.writeheader()
        writer.writerows(results)

    with rollback_path.open("w", encoding="utf-8") as file:
        json.dump(rollback_items, file, ensure_ascii=False, indent=2)

    return {
        "log_jsonl": str(jsonl_path),
        "result_csv": str(csv_path),
        "rollback_file": str(rollback_path),
        "processed_count": len(results),
        "success_count": sum(1 for item in results if item["status"] == "success"),
    }
