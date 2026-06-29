from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

SUPPORTED_EXTS_SET = frozenset({
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".jpg", ".jpeg", ".tif", ".tiff",
    ".xlsx", ".xls", ".eml",
})

if TYPE_CHECKING:
    from sharepoint_client import SharePointClient

SUPPORTED_EXTS = frozenset({
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".jpg", ".jpeg", ".tif", ".tiff",
    ".xlsx", ".xls", ".eml",
})

# GPT-4.1-mini: ~3800 input tokens + ~700 output tokens per file (legal metadata) ≈ $0.0028
LLM_COST_PER_FILE_USD = 0.0028


def run_survey(sp_client: "SharePointClient", config: dict, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    file_counter = [0]

    def on_file(n: int) -> None:
        file_counter[0] += n
        print(f"\r[survey] 폴더 탐색 중... 발견 파일: {file_counter[0]:,}개", end="", flush=True)

    print("[survey] SharePoint 폴더 트리 수집 시작...")
    tree = sp_client.build_folder_tree(progress_callback=on_file)
    print()

    total_files = tree["total_files"]
    total_size = tree["total_size"]
    ext_breakdown = _count_by_ext(tree)
    supported_count = sum(cnt for ext, cnt in ext_breakdown.items() if ext in SUPPORTED_EXTS)
    unsupported_count = total_files - supported_count

    pdf_count = ext_breakdown.get(".pdf", 0)
    image_count = sum(ext_breakdown.get(e, 0) for e in (".jpg", ".jpeg", ".tif", ".tiff"))
    ocr_files = pdf_count + image_count
    est_seconds = supported_count * 3.5
    est_cost = supported_count * LLM_COST_PER_FILE_USD

    print()
    print("=" * 72)
    print(f"  SharePoint 파일 현황 — {sp_client.site_url}")
    print("=" * 72)
    print(f"  총 파일 수         : {total_files:,}")
    print(f"  총 크기            : {_human_size(total_size)}")
    print(f"  처리 가능 파일     : {supported_count:,}")
    print(f"  처리 불가 파일     : {unsupported_count:,}")
    print()

    print("  [확장자별 파일 수]")
    for ext, cnt in sorted(ext_breakdown.items(), key=lambda x: -x[1]):
        mark = "✓" if ext in SUPPORTED_EXTS else "✗"
        print(f"    {mark} {ext:<14} {cnt:>7,}")
    print()

    print("  [폴더 구조]  (해당 폴더 직접 파일수 / 하위 전체 합계)")
    _print_tree(tree, indent=0, max_depth=5)
    print()

    print("  [처리 비용 예측]")
    print(f"    LLM 처리 대상    : {supported_count:,} 파일")
    print(f"    OCR 처리 대상    : {ocr_files:,} 파일  (PDF + 이미지)")
    print(f"    예상 처리 시간   : {_format_duration(est_seconds)}")
    print(f"    예상 LLM 비용    : ${est_cost:,.2f}  (GPT-4.1-mini 기준)")
    print("=" * 72)
    print()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_rows: list[dict] = []
    _collect_folder_rows(tree, folder_rows)

    survey_data = {
        "surveyed_at": timestamp,
        "site_url": sp_client.site_url,
        "summary": {
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_human": _human_size(total_size),
            "supported_files": supported_count,
            "unsupported_files": unsupported_count,
        },
        "ext_breakdown": dict(sorted(ext_breakdown.items(), key=lambda x: -x[1])),
        "cost_estimate": {
            "llm_files": supported_count,
            "ocr_files": ocr_files,
            "est_total_seconds": round(est_seconds),
            "est_duration_human": _format_duration(est_seconds),
            "est_cost_usd": round(est_cost, 2),
        },
        "folders": folder_rows,
    }

    json_path = output_dir / f"sp_survey_{timestamp}.json"
    json_path.write_text(json.dumps(survey_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[survey] 결과 저장: {json_path}")
    return 0


def build_survey_data(tree: dict, site_url: str) -> dict:
    """Return structured survey data dict for API and console output."""
    total_files = tree["total_files"]
    total_size = tree["total_size"]
    ext_breakdown = _count_by_ext(tree)
    supported_count = sum(cnt for ext, cnt in ext_breakdown.items() if ext in SUPPORTED_EXTS)
    unsupported_count = total_files - supported_count

    pdf_count = ext_breakdown.get(".pdf", 0)
    image_count = sum(ext_breakdown.get(e, 0) for e in (".jpg", ".jpeg", ".tif", ".tiff"))
    ocr_files = pdf_count + image_count
    est_seconds = supported_count * 3.5
    est_cost = supported_count * LLM_COST_PER_FILE_USD

    folder_rows: list[dict] = []
    _collect_folder_rows(tree, folder_rows)

    return {
        "site_url": site_url,
        "total_files": total_files,
        "total_size": total_size,
        "total_size_human": _human_size(total_size),
        "supported_files": supported_count,
        "unsupported_files": unsupported_count,
        "ext_breakdown": [
            {"ext": k, "count": v, "supported": k in SUPPORTED_EXTS}
            for k, v in sorted(ext_breakdown.items(), key=lambda x: -x[1])
        ],
        "cost_estimate": {
            "llm_files": supported_count,
            "ocr_files": ocr_files,
            "est_seconds": round(est_seconds),
            "est_duration": _format_duration(est_seconds),
            "est_cost_usd": round(est_cost, 2),
        },
        "folder_tree": _serialize_tree_node(tree),
        "folders": folder_rows,
    }


def _natural_key(name: str) -> list:
    """Split name into text/number segments so '10. x' sorts after '9. x'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', name)]


def _serialize_tree_node(node: dict) -> dict:
    serialized_children = [
        _serialize_tree_node(c)
        for c in sorted(node["children"], key=lambda c: _natural_key(c["name"]))
    ]
    direct_supported   = sum(1 for f in node["files"] if f["ext"] in SUPPORTED_EXTS)
    direct_unsupported = len(node["files"]) - direct_supported
    child_supported    = sum(c["supported_files"]   for c in serialized_children)
    child_unsupported  = sum(c["unsupported_files"] for c in serialized_children)
    return {
        "name": node["name"],
        "path": node["path"],
        "direct_files": len(node["files"]),
        "total_files": node["total_files"],
        "total_size": node["total_size"],
        "supported_files":   direct_supported   + child_supported,
        "unsupported_files": direct_unsupported + child_unsupported,
        "children": serialized_children,
    }


def _count_by_ext(node: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in node["files"]:
        ext = f["ext"] or "(no ext)"
        counts[ext] = counts.get(ext, 0) + 1
    for child in node["children"]:
        for ext, cnt in _count_by_ext(child).items():
            counts[ext] = counts.get(ext, 0) + cnt
    return counts


def _collect_folder_rows(node: dict, result: list[dict]) -> None:
    ext_counts: dict[str, int] = {}
    for f in node["files"]:
        ext = f["ext"] or "(no ext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    result.append({
        "path": node["path"],
        "direct_files": len(node["files"]),
        "subtree_files": node["total_files"],
        "direct_size_bytes": sum(f["size"] for f in node["files"]),
        "ext_counts": dict(sorted(ext_counts.items(), key=lambda x: -x[1])),
    })
    for child in sorted(node["children"], key=lambda c: c["path"]):
        _collect_folder_rows(child, result)


def _print_tree(node: dict, indent: int, max_depth: int) -> None:
    prefix = "  " + "    " * indent
    direct = len(node["files"])
    total = node["total_files"]
    name = node["name"]
    if indent == 0:
        print(f"{prefix}[{name}]  {direct} / {total:,}")
    else:
        print(f"{prefix}└─ {name}  ({direct} / {total:,})")
    if indent < max_depth:
        for child in sorted(node["children"], key=lambda c: -c["total_files"]):
            _print_tree(child, indent + 1, max_depth)
    elif node["children"]:
        child_files = sum(c["total_files"] for c in node["children"])
        print(f"{prefix}    ... {len(node['children'])}개 하위 폴더 ({child_files:,}개 파일)")


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def save_survey_cache(cache_path: Path, survey_data: dict, delta_link: str, file_index: dict,
                      folder_sharing_url: str = "") -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "scanned_at": datetime.now().isoformat(),
        "site_url": survey_data.get("site_url", ""),
        "folder_sharing_url": folder_sharing_url,
        "delta_link": delta_link,
        "survey_data": survey_data,
        "file_index": file_index,
    }
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_survey_cache(cache_path: Path) -> dict | None:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "survey_data" not in data:
            return None
        return data
    except Exception:
        return None


def compute_delta_changes(old_index: dict, delta_items: list[dict]) -> dict:
    """Compare delta items against old_index. Returns {added, deleted, modified}."""
    _DRIVE_ROOT_RE = re.compile(r"^/drives/[^/]+/root:", re.IGNORECASE)
    added, deleted, modified = [], [], []

    for item in delta_items:
        item_id = item["id"]
        is_deleted = bool(item.get("deleted"))

        if is_deleted:
            if item_id in old_index:
                deleted.append({"id": item_id, **old_index[item_id]})
        elif "file" in item:
            parent_raw = item.get("parentReference", {}).get("path", "")
            folder_path = _DRIVE_ROOT_RE.sub("", parent_raw)
            new_info = {
                "name": item["name"],
                "folder_path": folder_path,
                "size": int(item.get("size") or 0),
                "modified": item.get("lastModifiedDateTime", ""),
                "ext": Path(item["name"]).suffix.lower(),
            }
            if item_id not in old_index:
                added.append({"id": item_id, **new_info})
            else:
                old = old_index[item_id]
                if old.get("modified") != new_info["modified"] or old.get("name") != new_info["name"]:
                    modified.append({
                        "id": item_id,
                        "old_name": old.get("name"),
                        "new_name": new_info["name"],
                        "folder_path": folder_path,
                        "modified": new_info["modified"],
                        "ext": new_info["ext"],
                        "size": new_info["size"],
                    })

    return {"added": added, "deleted": deleted, "modified": modified}


def _format_duration(seconds: float) -> str:
    secs = int(seconds)
    h, rem = divmod(secs, 3600)
    m = rem // 60
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}일 {h}시간 {m}분"
    if h > 0:
        return f"{h}시간 {m}분"
    return f"{secs // 60}분 {secs % 60}초"
