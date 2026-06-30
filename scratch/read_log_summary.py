import json
from pathlib import Path

log_file = Path("C:/Users/yoonjoo2026/.gemini/antigravity/brain/682e1b01-6f72-43b3-ad5e-3ea85b8be1e5/.system_generated/tasks/task-86.log")
if log_file.exists():
    content = log_file.read_text(encoding="utf-8", errors="ignore")
    # Find the start of JSON output (which is at the end of the log)
    # The JSON block looks like { "log_jsonl": ... }
    start_idx = content.rfind('{\n  "log_jsonl":')
    if start_idx != -1:
        json_str = content[start_idx:]
        try:
            summary = json.loads(json_str)
            # Print only key summary fields
            print("=== Execution Summary ===")
            print(f"Processed Count : {summary.get('processed_count')}")
            print(f"Success Count   : {summary.get('success_count')}")
            print(f"Log JSONL       : {summary.get('log_jsonl')}")
            print(f"Result CSV      : {summary.get('result_csv')}")
            print(f"Rollback File   : {summary.get('rollback_file')}")
        except Exception as e:
            print(f"Error parsing JSON summary: {e}")
            # print last 5 lines as fallback, replacing non-ascii
            lines = json_str.splitlines()
            for line in lines[-10:]:
                print(line.encode('ascii', errors='replace').decode('ascii'))
    else:
        print("JSON summary pattern not found at the end of log.")
else:
    print("Log file not found.")
