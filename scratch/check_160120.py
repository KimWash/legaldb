import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import load_config
from excel_writer import read_review_rows
from sharepoint_client import SharePointClient

def main():
    xlsx_path = PROJECT_ROOT / "rename_review_20260630_160120.xlsx"
    if not xlsx_path.exists():
        print(f"Error: {xlsx_path.name} not found")
        return
        
    print(f"Reading rows from {xlsx_path.name}...")
    rows = read_review_rows(xlsx_path)
    approved_rows = [r for r in rows if str(r.get("approval") or "").strip().upper() == "Y"]
    print(f"Total rows: {len(rows)}, Approved ('Y'): {len(approved_rows)}")
    
    if not approved_rows:
        print("No approved items to process.")
        return
        
    # Check first item to determine correct SharePoint site (DB2 vs DX-DB)
    test_item = approved_rows[0]
    item_id = test_item.get("sharepoint_item_id")
    print(f"Testing item_id: {item_id}")
    
    config = load_config(PROJECT_ROOT / "config.yaml")
    sp_cfg = config.get("sharepoint", {})
    
    # Try sites/DB2 first
    sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DB2"
    sp_cfg["folder_sharing_url"] = ""
    sp_cfg["drive_name"] = "Documents"
    
    token_cache_path = PROJECT_ROOT / "temp" / "sp_token_cache.json"
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()
    
    print("Resolving drive for sites/DB2...")
    try:
        drive_id = client._resolve_drive()
        print(f"sites/DB2 Drive ID: {drive_id}")
        
        # Test item retrieval on DB2
        print(f"Testing retrieval on sites/DB2...")
        client._get(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}")
        print("SUCCESS: Target site is sites/DB2!")
        sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DB2"
    except Exception as e:
        print(f"sites/DB2 failed: {e}")
        print("Fallback to sites/DX-DB...")
        # Try sites/DX-DB
        try:
            sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DX-DB"
            client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
            client.authenticate()
            drive_id = client._resolve_drive()
            client._get(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}")
            print("SUCCESS: Target site is sites/DX-DB!")
        except Exception as e2:
            print(f"sites/DX-DB failed as well: {e2}")

if __name__ == "__main__":
    main()
