"""Single-file SharePoint analysis test.

Usage
-----
# SharePoint 공유 링크로 직접 지정 (가장 쉬운 방법)
python test_sp_single.py --sharing-url "https://poscointltest.sharepoint.com/:b:/s/DB/IQ...?e=xxx"

# 드라이브 루트 기준 상대 경로로 지정
python test_sp_single.py --file-path "법무DB/계약/계약서.pdf"

# config.yaml의 root_folder 기준 상대 경로로 지정
python test_sp_single.py --file-path "계약/계약서.pdf" --relative-to-root-folder

# OCR/LLM 없이 구조만 빠르게 확인
python test_sp_single.py --sharing-url "https://..." --fast

결과: ./output/review/sp_test_*.xlsx 로 저장됩니다.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import ensure_directories, load_config, resolve_project_path
from excel_writer import write_review_workbook
from extractor_docx import extract_docx
from extractor_eml import extract_eml
from extractor_image import extract_image
from extractor_pdf import extract_pdf
from extractor_pptx import extract_pptx
from extractor_xlsx import extract_xlsx
from llm_client import OllamaClient
from models import AnalysisRecord, FileRecord
from naming_engine import mark_conflicts, propose_name
from sharepoint_client import SharePointClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SharePoint 단일 파일 분석 테스트")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--sharing-url",
        help="SharePoint 공유 링크 URL (브라우저 주소창 또는 '링크 복사' 값)",
    )
    group.add_argument(
        "--file-path",
        help="드라이브 루트 기준 파일 경로 (예: 법무DB/계약/계약서.pdf)",
    )
    parser.add_argument(
        "--relative-to-root-folder",
        action="store_true",
        help="--file-path를 config.yaml의 sharepoint.root_folder 기준으로 해석",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config.yaml"),
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="OCR / LLM 비활성화 (빠른 구조 확인용)",
    )
    return parser.parse_args()


def _extract(path: Path, ext: str, config: dict, temp_dir: Path):
    max_chars = int(config.get("performance", {}).get("extract_max_chars", 8000))
    if ext == ".pdf":
        return extract_pdf(path, config, temp_dir, max_chars=max_chars)
    if ext == ".docx":
        return extract_docx(path, max_chars=max_chars)
    if ext == ".pptx":
        return extract_pptx(path, max_chars=max_chars)
    if ext in (".xlsx", ".xls"):
        return extract_xlsx(path, max_chars=max_chars)
    if ext in (".jpg", ".jpeg", ".tif", ".tiff"):
        return extract_image(path, config, max_chars=max_chars)
    if ext == ".eml":
        return extract_eml(path, max_chars=max_chars)
    from models import ExtractionResult
    return ExtractionResult(
        file_type=ext.lstrip(".") or "unknown",
        extraction_status="manual_review_required",
        notes=["지원되지 않는 형식이거나 수동 변환이 필요합니다."],
    )


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.fast:
        config.setdefault("ocr", {})["enabled"] = False
        config.setdefault("llm", {})["enabled"] = False

    ensure_directories(config, PROJECT_ROOT)
    temp_dir = resolve_project_path(PROJECT_ROOT, config["temp"]["dir"])
    tempfile.tempdir = str(temp_dir)
    review_dir = resolve_project_path(PROJECT_ROOT, config["review"]["output_dir"])

    sp_cfg = config.get("sharepoint", {})
    token_cache_path = resolve_project_path(
        PROJECT_ROOT, sp_cfg.get("token_cache_path", "./temp/sp_token_cache.json")
    )
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()

    # ── 파일 메타데이터 조회 ──────────────────────────────────────────────
    if args.sharing_url:
        print(f"[test] 공유 링크로 파일 조회 중...")
        item = client.get_item_by_sharing_url(args.sharing_url)
    else:
        file_path_arg = args.file_path.strip("/")
        if args.relative_to_root_folder and sp_cfg.get("root_folder"):
            drive_path = f"{sp_cfg['root_folder'].strip('/')}/{file_path_arg}"
        else:
            drive_path = file_path_arg
        print(f"[test] 경로로 파일 조회: {drive_path}")
        item = client.get_item_by_path(drive_path)

    name: str = item["name"]
    ext = Path(name).suffix.lower()
    print(f"[test] ✓ 파일 확인")
    print(f"  이름       : {name}")
    print(f"  크기       : {item.get('size', 0):,} bytes")
    print(f"  수정일시   : {item.get('lastModifiedDateTime', '-')}")
    print(f"  item ID    : {item['id']}")
    print(f"  webUrl     : {item.get('webUrl', '-')}")

    folder_abs = client.item_folder_path(item)
    relative_path = client.item_relative_path(item)
    full_display_path = f"{folder_abs.rstrip('/')}/{name}"
    print(f"  폴더경로   : {folder_abs}")
    print(f"  상대경로   : {relative_path}")

    record = FileRecord(
        seq=1,
        root_path=sp_cfg.get("root_folder", "/"),
        original_full_path=full_display_path,
        original_dir_path=folder_abs,
        original_file_name=name,
        file_extension=ext,
        file_size=item.get("size", 0),
        last_modified_time=item.get("lastModifiedDateTime", ""),
        relative_path_from_root=relative_path,
        supported=True,
        sharepoint_item_id=item["id"],
        sharepoint_web_url=item.get("webUrl", ""),
    )

    # ── 다운로드 ──────────────────────────────────────────────────────────
    sp_dl_dir = temp_dir / "sp_downloads"
    print(f"\n[test] 파일 다운로드 중...")
    local_path = client.download_file(item["id"], name, sp_dl_dir)
    print(f"[test] ✓ 다운로드 완료: {local_path.name} ({local_path.stat().st_size:,} bytes)")

    # ── 텍스트 추출 ───────────────────────────────────────────────────────
    print(f"\n[test] 텍스트 추출 중 (ext={ext}, ocr={config.get('ocr', {}).get('enabled', True)})...")
    extraction = _extract(local_path, ext, config, temp_dir)
    print(f"[test] ✓ 추출 완료")
    print(f"  상태       : {extraction.extraction_status}")
    print(f"  OCR 사용   : {extraction.ocr_used}")
    print(f"  페이지 수  : {extraction.page_count}")
    if extraction.notes:
        print(f"  노트       : {extraction.notes}")
    if extraction.text_excerpt:
        preview = extraction.text_excerpt[:300].replace("\n", " ").strip()
        print(f"  텍스트 미리보기: {preview}...")

    # ── LLM 메타데이터 ────────────────────────────────────────────────────
    llm_result = None
    if (
        config.get("llm", {}).get("enabled", True)
        and extraction.extraction_status == "success"
        and extraction.text_excerpt
    ):
        print(f"\n[test] LLM 메타데이터 추출 중...")
        llm = OllamaClient(config, PROJECT_ROOT)
        excerpt_chars = int(config.get("performance", {}).get("llm_excerpt_chars", 5000))
        source_text = extraction.extracted_text or extraction.text_excerpt
        llm_result = llm.extract_metadata(
            {
                "file_path": record.original_full_path,
                "file_extension": record.file_extension,
                "parent_folder": Path(record.original_dir_path).name,
                "relative_path": record.relative_path_from_root,
                "extracted_text_excerpt": source_text[:excerpt_chars],
            }
        )
        print(f"[test] ✓ LLM 결과:")
        print(f"  {json.dumps(llm_result, ensure_ascii=False)[:400]}")

    # ── 파일명 제안 ───────────────────────────────────────────────────────
    naming = propose_name(record, extraction, llm_result, config)
    analysis = AnalysisRecord(record, extraction, naming)
    mark_conflicts([analysis])

    print(f"\n[test] ── 파일명 제안 결과 ───────────────────────────────")
    print(f"  제안 파일명: {naming.suggested_file_name}")
    print(f"  신뢰도     : {naming.confidence:.2f}")
    print(f"  수동검토   : {naming.needs_manual_review}")
    print(f"  사유       : {naming.reason}")

    # ── 리뷰 Excel 저장 ───────────────────────────────────────────────────
    workbook_path = write_review_workbook([analysis], review_dir)
    print(f"\n[test] ✓ 리뷰 Excel 저장: {workbook_path}")
    print(f"\n[안내] Excel에서 approval 컬럼에 'Y'를 입력한 뒤 아래 명령으로 실제 이름 변경:")
    print(f"  python src/main.py --mode rename --source sharepoint --review-file \"{workbook_path}\"")


if __name__ == "__main__":
    main()
