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
    # Silent auth
    client.authenticate()
    
    # Test batch with dummy/invalid items to see if endpoint rejects the payload format
    test_items = [
        {"item_id": "017JC4QUNT2EJ7N4BXLVEJ27TD2JGF3AKI", "new_name": "test_batch_rename_dummy.pdf"}
    ]
    
    print("Sending test batch request...")
    try:
        results = client.batch_rename_items(test_items)
        print("Success! Batch response received:")
        for r in results:
            print(r)
    except Exception as e:
        print("Error during batch request!")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
