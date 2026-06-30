import sys
from pathlib import Path
import os

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
    token_cache_path = PROJECT_ROOT / "temp" / "sp_token_cache.json"
    
    client = SharePointClient(sp_cfg, token_cache_path=token_cache_path)
    client.authenticate()
    
    # 1. Get a real file item from SharePoint to test
    print("Listing files to find a test file...")
    files = client.list_files_recursive()
    if not files:
        print("No files found in SharePoint drive!")
        return
        
    test_file = None
    for f in files:
        if "test" in f["name"].lower() or f["name"].endswith(".pdf"):
            test_file = f
            break
    if not test_file:
        test_file = files[0]
        
    print(f"Selected test file:")
    print(f"  Name: {test_file['name']}")
    print(f"  ID  : {test_file['id']}")
    
    original_name = test_file["name"]
    original_path = client.item_folder_path(test_file).rstrip("/") + "/" + original_name
    
    suffix = Path(original_name).suffix
    stem = Path(original_name).stem
    new_name = f"{stem}_exec_batchtest{suffix}"
    suggested_path = client.item_folder_path(test_file).rstrip("/") + "/" + new_name
    
    # Construct review_rows structure similar to Excel reader outputs
    review_rows = [
        {
            "approval": "Y",
            "sharepoint_item_id": test_file["id"],
            "original_full_path": original_path,
            "suggested_full_path": suggested_path
        }
    ]
    
    logs_dir = PROJECT_ROOT / "output" / "logs"
    rollback_dir = PROJECT_ROOT / "output" / "rollback"
    
    print("\nRunning execute_rename using our new batch logic...")
    result = execute_rename(
        review_rows=review_rows,
        config=config,
        logs_dir=logs_dir,
        rollback_dir=rollback_dir,
        sp_client=client,
        site_url=sp_cfg.get("site_url", "")
    )
    
    print("\nResult returned from execute_rename:")
    print(result)
    
    # Check if name actually changed on SharePoint
    drive_id = client._resolve_drive()
    updated_item = client._get(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{test_file['id']}")
    print(f"\nActual name on SharePoint after rename: {updated_item.get('name')}")
    
    if updated_item.get("name") == new_name:
        print("SUCCESS: File successfully renamed via batch executor!")
        
        # Cleanup: Revert file name to original
        print("Cleaning up: Reverting file name to original...")
        client.rename_item(test_file['id'], original_name)
        reverted_item = client._get(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{test_file['id']}")
        print(f"Name reverted to: {reverted_item.get('name')}")
    else:
        print("FAILED: File name was NOT updated on SharePoint!")

if __name__ == "__main__":
    main()
