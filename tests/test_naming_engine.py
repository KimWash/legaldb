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
