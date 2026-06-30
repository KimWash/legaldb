import sys
from pathlib import Path

# Add src to python path
src_path = Path("d:/Legal_DB_Rename_Project_MSsharepoint/src")
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from excel_writer import read_review_rows

excel_file = Path("d:/Legal_DB_Rename_Project_MSsharepoint/rename_review_20260630_160120.xlsx")
rows = read_review_rows(excel_file)
print(f"Total rows in Excel: {len(rows)}")

different_names = 0
out_of_scope = 0
needs_manual = 0
conflicts = 0
y_approvals = 0

for i, r in enumerate(rows):
    orig = r.get("original_file_name")
    sugg = r.get("suggested_file_name")
    status = r.get("rename_status")
    approval = r.get("approval")
    manual = r.get("needs_manual_review")
    
    if approval == "Y":
        y_approvals += 1
    if orig != sugg:
        different_names += 1
    if status == "out_of_scope":
        out_of_scope += 1
    if status == "duplicate_conflict":
        conflicts += 1
    if manual == "True" or manual is True or str(manual).strip().upper() == "TRUE" or str(manual).strip().upper() == "Y":
        needs_manual += 1

print(f"Rows with original != suggested: {different_names}")
print(f"Out of scope rows: {out_of_scope}")
print(f"Conflict rows: {conflicts}")
print(f"Needs manual review: {needs_manual}")
print(f"Approval = 'Y': {y_approvals}")
