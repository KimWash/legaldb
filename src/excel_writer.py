from __future__ import annotations

from pathlib import Path
from datetime import datetime

from models import AnalysisRecord


HEADERS = [
    "seq", "relative_path", "original_folder", "original_file_name", "original_full_path",
    "file_extension", "file_size", "last_modified_time", "file_type", "extraction_status",
    "ocr_used", "extracted_doc_type", "extracted_summary", "extracted_document_title", "extracted_case_name", "extracted_institution",
    "extracted_date", "extracted_keyword", "suggested_file_name", "suggested_full_path",
    "reason", "confidence", "needs_manual_review", "approval", "reviewer_comment",
    "rename_status", "rollback_name",
]


def write_review_workbook(records: list[AnalysisRecord], output_dir: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet import _writer as worksheet_writer

    def safe_cleanup(self) -> None:
        import os

        if getattr(self, "out", None) is not None:
            worksheet_writer.ALL_TEMP_FILES = [path for path in worksheet_writer.ALL_TEMP_FILES if path != self.out]
            try:
                os.remove(self.out)
            except PermissionError:
                pass

    worksheet_writer.WorksheetWriter.cleanup = safe_cleanup

    output_path = output_dir / f"rename_review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "rename_review"
    sheet.append(HEADERS)

    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    warning_fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    conflict_fill = PatternFill(fill_type="solid", fgColor="F4CCCC")

    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

    for record in records:
        row = record.to_excel_row()
        sheet.append([row.get(header, "") for header in HEADERS])
        row_index = sheet.max_row
        if bool(row["needs_manual_review"]):
            for cell in sheet[row_index]:
                cell.fill = warning_fill
        if str(row["rename_status"]) == "duplicate_conflict":
            for cell in sheet[row_index]:
                cell.fill = conflict_fill

    validation = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    sheet.add_data_validation(validation)
    approval_col = HEADERS.index("approval") + 1
    validation.add(f"{sheet.cell(row=2, column=approval_col).coordinate}:{sheet.cell(row=max(sheet.max_row, 2), column=approval_col).coordinate}")

    for column, width in {"B": 45, "C": 35, "D": 45, "E": 70, "L": 20, "M": 28, "N": 20, "Q": 45, "R": 70, "S": 60, "W": 22}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    workbook.save(output_path)
    return output_path


def read_review_rows(review_file: Path) -> list[dict]:
    from openpyxl import load_workbook

    workbook = load_workbook(review_file)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    return [{headers[index]: value for index, value in enumerate(row)} for row in sheet.iter_rows(min_row=2, values_only=True)]
