import sys
import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import load_config
from sharepoint_client import SharePointClient
from rename_executor import execute_rename

def main():
    config = load_config(PROJECT_ROOT / "config.yaml")
    sp_cfg = config.get("sharepoint", {})
    
    # ── Force the site URL to sites/DB2 (matching the rollback metadata) ──
    # The previous mismatch was because config.yaml points to sites/DX-DB
    # but the actual files to rename belong to sites/DB2.
    sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DB2"
    sp_cfg["folder_sharing_url"] = "" # Clear the DX-DB sharing link to trigger REST API drive resolution for DB2
    sp_cfg["drive_name"] = "Documents"
    
    token_cache_path = PROJECT_ROOT / "temp" / "sp_token_cache.json"
    
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()
    
    csv_path = PROJECT_ROOT / "output" / "logs" / "rename_result_20260630_150305.csv"
    if not csv_path.exists():
        print(f"Error: CSV log not found at {csv_path}")
        return
        
    print(f"Reading target records from {csv_path.name}...")
    review_rows = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            review_rows.append({
                "approval": "Y",
                "sharepoint_item_id": row["sharepoint_item_id"],
                "original_full_path": row["original_full_path"],
                "suggested_full_path": row["new_full_path"]
            })
            
    print(f"Total items loaded for retry: {len(review_rows)}")
    
    logs_dir = PROJECT_ROOT / "output" / "logs"
    rollback_dir = PROJECT_ROOT / "output" / "rollback"
    
    print("\nExecuting rename retry with updated mapping batch logic on sites/DB2...")
    result = execute_rename(
        review_rows=review_rows,
        config=config,
        logs_dir=logs_dir,
        rollback_dir=rollback_dir,
        sp_client=client,
        site_url=sp_cfg["site_url"]
    )
    
    print("\nExecution finished. Results summary:")
    print(f"  Processed Count : {result['processed_count']}")
    print(f"  Success Count   : {result['success_count']}")
    print(f"  Result CSV      : {result['result_csv']}")
    print(f"  Rollback File   : {result['rollback_file']}")

if __name__ == "__main__":
    main()
