import sys
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import load_config
from sharepoint_client import SharePointClient
from rename_executor import execute_rename

def load_rows_from_csv(csv_path: Path, rows_dict: dict):
    if not csv_path.exists():
        print(f"Warning: CSV file not found at {csv_path}")
        return
    print(f"Loading records from {csv_path.name}...")
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id = row["sharepoint_item_id"].strip()
            if not item_id:
                continue
            rows_dict[item_id] = {
                "approval": "Y",
                "sharepoint_item_id": item_id,
                "original_full_path": row["original_full_path"],
                "suggested_full_path": row["new_full_path"]
            }

def main():
    config = load_config(PROJECT_ROOT / "config.yaml")
    sp_cfg = config.get("sharepoint", {})
    
    # Target the correct sites/DB2 context
    sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DB2"
    sp_cfg["folder_sharing_url"] = "" 
    sp_cfg["drive_name"] = "Documents"
    
    token_cache_path = PROJECT_ROOT / "temp" / "sp_token_cache.json"
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()
    
    # Collect unique rows by sharepoint_item_id from both CSV files
    rows_dict = {}
    csv1 = PROJECT_ROOT / "rename_result_20260630_145231.csv"
    csv2 = PROJECT_ROOT / "rename_result_20260630_145529.csv"
    
    load_rows_from_csv(csv1, rows_dict)
    load_rows_from_csv(csv2, rows_dict)
    
    review_rows = list(rows_dict.values())
    print(f"Total unique items loaded for retry: {len(review_rows)}")
    if not review_rows:
        print("No items to rename.")
        return
        
    logs_dir = PROJECT_ROOT / "output" / "logs"
    rollback_dir = PROJECT_ROOT / "output" / "rollback"
    
    print("\nExecuting batch rename retry on sites/DB2...")
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
