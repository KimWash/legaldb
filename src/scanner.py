from __future__ import annotations

from pathlib import Path
from datetime import datetime

from models import FileRecord


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
