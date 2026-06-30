from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import FileRecord, ExtractionResult
from naming_engine import propose_name


def test_propose_name_builds_filename():
    file_record = FileRecord(
        seq=1,
        root_path="D:/root",
        original_full_path="D:/root/case/sample.pdf",
        original_dir_path="D:/root/case",
        original_file_name="sample.pdf",
        file_extension=".pdf",
        file_size=1,
        last_modified_time="2026-04-09T10:00:00",
        relative_path_from_root="case/sample.pdf",
        supported=True,
    )
    extraction = ExtractionResult(file_type="pdf-text", extraction_status="success", extracted_text="Statement of Claim 2024.05.14", text_excerpt="Statement of Claim 2024.05.14")
    config = {"naming": {"org_name": "법무실", "confidence_threshold": 0.85, "max_filename_length": 180}}
    result = propose_name(file_record, extraction, None, config)
    assert result.suggested_file_name.endswith(".pdf")
    assert "법무실" in result.suggested_file_name


def test_propose_name_skips_date_when_filename_ends_with_yymmdd():
    # Original filename ends with _yymmdd (e.g. _231024)
    file_record = FileRecord(
        seq=2,
        root_path="D:/root",
        original_full_path="D:/root/case/sample_231024.pdf",
        original_dir_path="D:/root/case",
        original_file_name="sample_231024.pdf",
        file_extension=".pdf",
        file_size=1,
        last_modified_time="2026-04-09T10:00:00",
        relative_path_from_root="case/sample_231024.pdf",
        supported=True,
    )
    # The text contains a different date to see if it gets extracted (it shouldn't)
    extraction = ExtractionResult(file_type="pdf-text", extraction_status="success", extracted_text="Statement of Claim 2024.05.14", text_excerpt="Statement of Claim 2024.05.14")
    config = {"naming": {"org_name": "법무실", "confidence_threshold": 0.85, "max_filename_length": 180}}
    result = propose_name(file_record, extraction, None, config)
    
    # 1. extracted_date should be '231024' (from the original filename)
    assert result.extracted_date == "231024"
    # 2. Suggested file name should contain the original date suffix ('231024') but NOT the one from text ('240514')
    assert "240514" not in result.suggested_file_name
    assert "231024" in result.suggested_file_name
    # 3. Date is successfully set, so it should not require manual review for "날짜 미확인"
    assert "날짜 미확인" not in result.reason


def test_propose_name_keeps_date_when_filename_does_not_end_with_yymmdd():
    # Original filename does NOT end with _yymmdd (e.g. has _rev2 at the end)
    file_record = FileRecord(
        seq=3,
        root_path="D:/root",
        original_full_path="D:/root/case/sample_231024_rev2.pdf",
        original_dir_path="D:/root/case",
        original_file_name="sample_231024_rev2.pdf",
        file_extension=".pdf",
        file_size=1,
        last_modified_time="2026-04-09T10:00:00",
        relative_path_from_root="case/sample_231024_rev2.pdf",
        supported=True,
    )
    extraction = ExtractionResult(file_type="pdf-text", extraction_status="success", extracted_text="Statement of Claim 2024.05.14", text_excerpt="Statement of Claim 2024.05.14")
    config = {"naming": {"org_name": "법무실", "confidence_threshold": 0.85, "max_filename_length": 180}}
    result = propose_name(file_record, extraction, None, config)
    
    # extracted_date should be extracted as '240514' (from text)
    assert result.extracted_date == "240514"
    assert "240514" in result.suggested_file_name
