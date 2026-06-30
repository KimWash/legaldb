import sys
from pathlib import Path

src_path = Path("d:/Legal_DB_Rename_Project_MSsharepoint/src")
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from excel_writer import read_review_rows

excel_file = Path("d:/Legal_DB_Rename_Project_MSsharepoint/rename_review_20260630_160120.xlsx")
rows = read_review_rows(excel_file)

count = 0
for r in rows:
    orig = r.get("original_file_name")
    sugg = r.get("suggested_file_name")
    status = r.get("rename_status")
    manual = r.get("needs_manual_review")
    reason = r.get("reason")
    
    if orig != sugg and status != "duplicate_conflict" and (manual == "True" or manual is True or str(manual).strip().upper() == "TRUE"):
        print(f"Row seq={r.get('seq')}:")
        print(f"  Original : {orig}")
        print(f"  Suggested: {sugg}")
        print(f"  Reason   : {reason}")
        print("-" * 50)
        count += 1
        if count >= 10:
            break
