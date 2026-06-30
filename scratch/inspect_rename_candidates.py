import sys
from pathlib import Path

src_path = Path("d:/Legal_DB_Rename_Project_MSsharepoint/src")
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from excel_writer import read_review_rows

excel_file = Path("d:/Legal_DB_Rename_Project_MSsharepoint/rename_review_20260630_160120.xlsx")
rows = read_review_rows(excel_file)

stats = {}
for r in rows:
    orig = r.get("original_file_name")
    sugg = r.get("suggested_file_name")
    if orig != sugg:
        status = r.get("rename_status")
        manual = r.get("needs_manual_review")
        key = (status, manual)
        stats[key] = stats.get(key, 0) + 1

print("Rows where original_file_name != suggested_file_name:")
for (status, manual), count in stats.items():
    print(f"  rename_status={status}, needs_manual_review={manual} -> {count} rows")
