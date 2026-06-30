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
    # SharePoint-specific fields (empty string when using local filesystem)
    sharepoint_item_id: str = ""
    sharepoint_web_url: str = ""
    sharepoint_drive_id: str = ""  # parentReference.driveId from MS Graph


@dataclass
class ExtractionResult:
    file_type: str
    extraction_status: str
    extracted_text: str = ""
    text_excerpt: str = ""
    page_count: int = 0
    ocr_used: bool = False
    ocr_quality_low: bool = False
    ocr_mean_confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class NamingResult:
    extracted_summary: str = ""
    document_abstract: str = ""
    extracted_document_title: str = ""
    extracted_original_title: str = ""
    extracted_doc_type: str = ""
    extracted_case_name: str = ""
    extracted_institution: str = ""
    extracted_date: str = ""
    extracted_keyword: str = ""
    revision_note: str = ""
    suggested_file_name: str = ""
    suggested_full_path: str = ""
    reason: str = ""
    confidence: float = 0.0
    needs_manual_review: bool = True
    rename_status: str = ""
    rollback_name: str = ""
    conflict_detected: bool = False
    manually_edited: bool = False


def _fmt_meta(v: Any) -> str:
    """Convert legal_metadata value to Excel-safe string."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    return str(v)


@dataclass
class AnalysisRecord:
    file_record: FileRecord
    extraction: ExtractionResult
    naming: NamingResult
    legal_metadata: dict = field(default_factory=dict)

    def to_excel_row(self) -> dict[str, Any]:
        m = self.legal_metadata
        row = {
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
            "document_abstract": self.naming.document_abstract,
            "extracted_document_title": self.naming.extracted_document_title,
            "extracted_original_title": self.naming.extracted_original_title,
            "extracted_case_name": self.naming.extracted_case_name,
            "extracted_institution": self.naming.extracted_institution,
            "extracted_date": self.naming.extracted_date,
            "extracted_keyword": self.naming.extracted_keyword,
            "revision_note": self.naming.revision_note,
            "suggested_file_name": self.naming.suggested_file_name,
            "suggested_full_path": self.naming.suggested_full_path,
            "manually_edited": "✓" if self.naming.manually_edited else "",
            "reason": self.naming.reason,
            "confidence": self.naming.confidence,
            "needs_manual_review": self.naming.needs_manual_review,
            "approval": "",
            "reviewer_comment": "",
            "rename_status": self.naming.rename_status,
            "rollback_name": self.naming.rollback_name,
            "sharepoint_item_id": self.file_record.sharepoint_item_id,
            "sharepoint_web_url": self.file_record.sharepoint_web_url,
            "sharepoint_drive_id": self.file_record.sharepoint_drive_id,
            # ── Legal metadata ──────────────────────────────────────────
            "case_name_normalized":   _fmt_meta(m.get("case_name_normalized")),
            "case_alias":             _fmt_meta(m.get("case_alias")),
            "case_type":              _fmt_meta(m.get("case_type")),
            "dispute_type":           _fmt_meta(m.get("dispute_type")),
            "document_category":      _fmt_meta(m.get("document_category")),
            "document_type_normalized": _fmt_meta(m.get("document_type_normalized")),
            "procedure_stage":        _fmt_meta(m.get("procedure_stage")),
            "document_purpose":       _fmt_meta(m.get("document_purpose")),
            "legal_issue_primary":    _fmt_meta(m.get("legal_issue_primary")),
            "legal_issue_secondary":  _fmt_meta(m.get("legal_issue_secondary")),
            "issue_tags":             _fmt_meta(m.get("issue_tags")),
            "claim_type":             _fmt_meta(m.get("claim_type")),
            "party_our_side":         _fmt_meta(m.get("party_our_side")),
            "party_counterparty":     _fmt_meta(m.get("party_counterparty")),
            "party_role":             _fmt_meta(m.get("party_role")),
            "law_firm_name_normalized": _fmt_meta(m.get("law_firm_name_normalized")),
            "institution_role":       _fmt_meta(m.get("institution_role")),
            "country_region":         _fmt_meta(m.get("country_region")),
            "amount_mentioned":       _fmt_meta(m.get("amount_mentioned")),
            "claim_amount":           _fmt_meta(m.get("claim_amount")),
            "currency":               _fmt_meta(m.get("currency")),
            "amount_context":         _fmt_meta(m.get("amount_context")),
            "event_date":             _fmt_meta(m.get("event_date")),
            "date_type":              _fmt_meta(m.get("date_type")),
            "next_action_date":       _fmt_meta(m.get("next_action_date")),
            "timeline_summary":       _fmt_meta(m.get("timeline_summary")),
            "lawyer_summary":         _fmt_meta(m.get("lawyer_summary")),
            "search_summary":         _fmt_meta(m.get("search_summary")),
            "recommended_use":        _fmt_meta(m.get("recommended_use")),
            "review_priority":        _fmt_meta(m.get("review_priority")),
            "review_priority_reason": _fmt_meta(m.get("review_priority_reason")),
            "metadata_limitations":   _fmt_meta(m.get("metadata_limitations")),
            "needs_legal_review":     _fmt_meta(m.get("needs_legal_review")),
        }
        return row

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "file_record": asdict(self.file_record),
            "extraction": asdict(self.extraction),
            "naming": asdict(self.naming),
            "legal_metadata": self.legal_metadata,
        }


def build_suggested_path(dir_path: str, filename: str) -> str:
    return str(Path(dir_path) / filename)
