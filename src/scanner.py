from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING

from models import FileRecord

if TYPE_CHECKING:
    from sharepoint_client import SharePointClient


def scan_sharepoint_files(client: "SharePointClient", config: dict) -> list[FileRecord]:
    """Scan a SharePoint document library and return FileRecord list.

    Downloads nothing — only metadata is fetched. The sharepoint_item_id
    field in each record is used later for on-demand download and rename.
    """
    supported_extensions = {ext.lower() for ext in config.get("supported_extensions", [])}
    exclude_extensions = {ext.lower() for ext in config.get("exclude_extensions", [])}

    items = client.list_files_recursive()
    items_sorted = sorted(
        items,
        key=lambda x: (
            client.item_folder_path(x),
            x.get("name", ""),
        ),
    )

    records: list[FileRecord] = []
    for seq, item in enumerate(items_sorted, start=1):
        name: str = item["name"]
        ext = Path(name).suffix.lower()
        folder_abs = client.item_folder_path(item)
        relative_path = client.item_relative_path(item)
        full_display_path = f"{folder_abs.rstrip('/')}/{name}"
        # lastModifiedDateTime from Graph API: "2024-01-15T10:30:00Z"
        modified = item.get("lastModifiedDateTime", "")
        supported = (
            ext in supported_extensions
            and ext not in exclude_extensions
            and not name.startswith("~$")
        )
        records.append(
            FileRecord(
                seq=seq,
                root_path=client.root_folder or "/",
                original_full_path=full_display_path,
                original_dir_path=folder_abs,
                original_file_name=name,
                file_extension=ext,
                file_size=item.get("size", 0),
                last_modified_time=modified,
                relative_path_from_root=relative_path,
                supported=supported,
                sharepoint_item_id=item.get("id", ""),
                sharepoint_web_url=item.get("webUrl", ""),
                sharepoint_drive_id=item.get("parentReference", {}).get("driveId", ""),
            )
        )
    return records


def scan_files(root_path: Path, config: dict) -> list[FileRecord]:
    supported_extensions = {ext.lower() for ext in config.get("supported_extensions", [])}
    exclude_extensions = {ext.lower() for ext in config.get("exclude_extensions", [])}
    iterator = root_path.rglob("*") if config.get("scan_recursive", True) else root_path.glob("*")

    records: list[FileRecord] = []
    for seq, path in enumerate(sorted([p for p in iterator if p.is_file()]), start=1):
        ext = path.suffix.lower()
        stat = path.stat()
        supported = ext in supported_extensions and ext not in exclude_extensions and not path.name.startswith("~$")
        records.append(
            FileRecord(
                seq=seq,
                root_path=str(root_path),
                original_full_path=str(path),
                original_dir_path=str(path.parent),
                original_file_name=path.name,
                file_extension=ext,
                file_size=stat.st_size,
                last_modified_time=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                relative_path_from_root=str(path.relative_to(root_path)),
                supported=supported,
            )
        )
    return records
