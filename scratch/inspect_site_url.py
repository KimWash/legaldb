import sys
from pathlib import Path

src_path = Path("d:/Legal_DB_Rename_Project_MSsharepoint/src")
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from excel_writer import read_review_rows

excel_file = Path("d:/Legal_DB_Rename_Project_MSsharepoint/rename_review_20260630_160120.xlsx")
rows = read_review_rows(excel_file)

sites = set()
for r in rows:
    url = r.get("sharepoint_web_url")
    if url:
        # Get site name from URL
        # e.g., https://poscointl1.sharepoint.com/sites/DB2/...
        parts = url.split("/")
        if len(parts) > 4 and parts[3] == "sites":
            sites.add(parts[4])
        else:
            sites.add(url)

print("Unique site names found in sharepoint_web_url:")
for s in sites:
    print(f"  {s}")
