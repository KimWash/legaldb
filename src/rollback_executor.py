from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json


def execute_rollback(rollback_file: Path) -> dict:
    items = json.loads(rollback_file.read_text(encoding="utf-8"))
    restored = 0
    skipped = 0
    failures: list[dict] = []
    for item in reversed(items):
        new_path = Path(item["new_full_path"])
        original_path = Path(item["original_full_path"])
        if not new_path.exists() or original_path.exists():
            skipped += 1
            continue
        try:
            new_path.rename(original_path)
            restored += 1
        except Exception as exc:
            failures.append(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "new_full_path": str(new_path),
                    "original_full_path": str(original_path),
                    "error": str(exc),
                }
            )
    return {"restored": restored, "skipped": skipped, "failures": failures}
