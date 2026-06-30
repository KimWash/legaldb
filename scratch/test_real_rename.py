import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import load_config
from sharepoint_client import SharePointClient

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
        
    # Find a test file (prefer PDF or DOCX, something safe)
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
    # Change name slightly
    suffix = Path(original_name).suffix
    stem = Path(original_name).stem
    new_name = f"{stem}_batchtest{suffix}"
    
    print(f"Attempting batch rename (with body as DICT) to: {new_name}")
    try:
        # We manually call a custom batch request to see if the rename is applied
        drive_id = client._resolve_drive()
        
        # Test 1: Dict body
        batch_requests_dict = {
            "requests": [
                {
                    "id": "1",
                    "method": "PATCH",
                    "url": f"/drives/{drive_id}/items/{test_file['id']}",
                    "headers": {"Content-Type": "application/json"},
                    "body": {"name": new_name}
                }
            ]
        }
        
        resp = client._post(f"https://graph.microsoft.com/v1.0/$batch", batch_requests_dict)
        r = resp.get("responses", [])[0]
        print(f"Response (Dict Body): status={r.get('status')}, body={r.get('body')}")
        
        # Check if name actually changed
        updated_item = client._get(f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{test_file['id']}")
        print(f"Actual name on SharePoint after Test 1: {updated_item.get('name')}")
        
        if updated_item.get("name") == new_name:
            print("Test 1 (Dict body) SUCCESS!")
            # Revert
            client.rename_item(test_file['id'], original_name)
        else:
            print("Test 1 (Dict body) FAILED to rename (even if status was 200).")
            
            # Test 2: String body
            print(f"Attempting batch rename (with body as JSON STRING) to: {new_name}")
            import json
            batch_requests_str = {
                "requests": [
                    {
                        "id": "1",
                        "method": "PATCH",
                        "url": f"/drives/{drive_id}/items/{test_file['id']}",
                        "headers": {"Content-Type": "application/json"},
                        "body": {"name": new_name} # We will try direct string serialization or look closely
                    }
                ]
            }
            # For Graph API batching, if body is a string, it must be the raw string payload.
            # However, Microsoft Graph batch request JSON spec has:
            # "body": { ... } inside requests or "body": "..." ? 
            # Actually, standard way in Graph API batching:
            # If Content-Type is application/json, 'body' MUST be a JSON object (or string in some SDKs, but gateway accepts object).
            # If so, why would Test 1 fail? Let's check.
            
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
