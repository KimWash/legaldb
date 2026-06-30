import json
from collections import Counter
from pathlib import Path

def main():
    log_path = Path(r"d:\Legal_DB_Rename_Project_MSsharepoint\output\logs\rename_log_20260630_151320.jsonl")
    if not log_path.exists():
        print(f"Error: Log file not found at {log_path}")
        return
        
    lines = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    print(f"Total lines: {len(lines)}")
    
    status_counts = Counter(l.get("status") for l in lines)
    print("Status distribution:")
    for k, v in status_counts.items():
        print(f"  {k}: {v}")
        
    fail_reasons = Counter(l.get("reason") for l in lines if l.get("status") != "success")
    print("Fail reasons (first 5 unique):")
    for k, v in fail_reasons.most_common(5):
        print(f"  {k}: {v}")
        
    if lines:
        print("\nFirst record sample:")
        print(json.dumps(lines[0], ensure_ascii=False, indent=2))
        
if __name__ == "__main__":
    main()
