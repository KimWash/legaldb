from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING
import json

if TYPE_CHECKING:
    from sharepoint_client import SharePointClient


def _item_in_scope(item: dict, folder_paths: list[str]) -> bool:
    """Return True if the rollback item's original path is under any of the folder_paths."""
    if not folder_paths:
        return True
    path = item.get("original_full_path", "").replace("\\", "/")
    for fp in folder_paths:
        if not fp:          # empty string = root = all files
            return True
        norm = fp.rstrip("/")
        if f"/{norm}/" in path or path.endswith(f"/{norm}"):
            return True
    return False


def _load_rollback_items(rollback_file: Path) -> list[dict]:
    """Load rollback items, supporting both legacy flat-array and envelope formats."""
    data = json.loads(rollback_file.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("items", [])


def preview_rollback(rollback_file: Path, folder_paths: list[str] | None = None) -> dict:
    """Return total and filtered item counts without executing."""
    items = _load_rollback_items(rollback_file)
    fps = folder_paths or []
    filtered = [it for it in items if _item_in_scope(it, fps)]
    return {"total_items": len(items), "filtered_items": len(filtered)}


def execute_rollback(
    rollback_file: Path,
    sp_client: "SharePointClient | None" = None,
    folder_paths: list[str] | None = None,
) -> dict:
    all_items = _load_rollback_items(rollback_file)
    fps = folder_paths or []
    items = [it for it in all_items if _item_in_scope(it, fps)]
    total_items = len(all_items)
    filtered_items = len(items)
    restored = 0
    skipped = 0
    failures: list[dict] = []

    for item in reversed(items):
        sp_item_id = str(item.get("sharepoint_item_id") or "").strip()
        original_name = item.get("original_file_name", "")

        if sp_item_id and sp_client is not None:
            # SharePoint rollback: rename back to original_file_name
            if not original_name:
                skipped += 1
                continue
            try:
                sp_client.rename_item(sp_item_id, original_name)
                restored += 1
            except Exception as exc:
                failures.append(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "sharepoint_item_id": sp_item_id,
                        "original_file_name": original_name,
                        "error": str(exc),
                    }
                )
        else:
            # Local filesystem rollback
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

    return {
        "restored": restored,
        "skipped": skipped,
        "failures": failures,
        "total_items": total_items,
        "filtered_items": filtered_items,
    }
