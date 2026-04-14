from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class FileRecord:
    seq: int
    root_path: str
    original_full_path: str
    original_dir_path: str
    original_file_name: str
    file_extension: str
    file_size: int
    last_modified_time: str
    relative_path_from_root: str
    supported: bool


@dataclass
class ExtractionResult:
    file_type: str
    extraction_status: str
    extracted_text: str = ""
    text_excerpt: str = ""
    page_count: int = 0
    ocr_used: bool = False
    ocr_quality_low: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class NamingResult:
    extracted_summary: str = ""
    extracted_document_title: str = ""
    extracted_doc_type: str = ""
    extracted_case_name: str = ""
    extracted_institution: str = ""
    extracted_date: str = ""
    extracted_keyword: str = ""
    suggested_file_name: str = ""
    suggested_full_path: str = ""
    reason: str = ""
    confidence: float = 0.0
    needs_manual_review: bool = True
    rename_status: str = ""
    rollback_name: str = ""
    conflict_detected: bool = False


@dataclass
class AnalysisRecord:
    file_record: FileRecord
    extraction: ExtractionResult
    naming: NamingResult

    def to_excel_row(self) -> dict[str, Any]:
        return {
            "seq": self.file_record.seq,
            "relative_path": self.file_record.relative_path_from_root,
            "original_folder": self.file_record.original_dir_path,
            "original_file_name": self.file_record.original_file_name,
            "original_full_path": self.file_record.original_full_path,
            "file_extension": self.file_record.file_extension,
            "file_size": self.file_record.file_size,
            "last_modified_time": self.file_record.last_modified_time,
            "file_type": self.extraction.file_type,
            "extraction_status": self.extraction.extraction_status,
            "ocr_used": self.extraction.ocr_used,
            "extracted_doc_type": self.naming.extracted_doc_type,
            "extracted_summary": self.naming.extracted_summary,
            "extracted_document_title": self.naming.extracted_document_title,
            "extracted_case_name": self.naming.extracted_case_name,
            "extracted_institution": self.naming.extracted_institution,
            "extracted_date": self.naming.extracted_date,
            "extracted_keyword": self.naming.extracted_keyword,
            "suggested_file_name": self.naming.suggested_file_name,
            "suggested_full_path": self.naming.suggested_full_path,
            "reason": self.naming.reason,
            "confidence": self.naming.confidence,
            "needs_manual_review": self.naming.needs_manual_review,
            "approval": "",
            "reviewer_comment": "",
            "rename_status": self.naming.rename_status,
            "rollback_name": self.naming.rollback_name,
        }

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "file_record": asdict(self.file_record),
            "extraction": asdict(self.extraction),
            "naming": asdict(self.naming),
        }


def build_suggested_path(dir_path: str, filename: str) -> str:
    return str(Path(dir_path) / filename)
