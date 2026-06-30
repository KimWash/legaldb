import sys
import json
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
    
    # Force DB2 site context matching previous DB2 target executions
    sp_cfg["site_url"] = "https://poscointl1.sharepoint.com/sites/DB2"
    sp_cfg["folder_sharing_url"] = ""
    sp_cfg["drive_name"] = "Documents"
    
    token_cache_path = PROJECT_ROOT / "temp" / "sp_token_cache.json"
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()
    
    jsonl_path = PROJECT_ROOT / "output" / "logs" / "rename_log_20260630_151320.jsonl"
    if not jsonl_path.exists():
        print(f"Error: JSONL log not found at {jsonl_path}")
        return
        
    print(f"Reading target records from {jsonl_path.name}...")
    review_rows = []
    with open(jsonl_path, mode="r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            review_rows.append({
                "approval": "Y",
                "sharepoint_item_id": data["sharepoint_item_id"],
                "original_full_path": data["original_full_path"],
                "suggested_full_path": data["new_full_path"]
            })
            
    print(f"Total items loaded for retry: {len(review_rows)}")
    
    logs_dir = PROJECT_ROOT / "output" / "logs"
    rollback_dir = PROJECT_ROOT / "output" / "rollback"
    
    print("\nExecuting rename retry with corrected batch mapping logic on sites/DB2...")
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
