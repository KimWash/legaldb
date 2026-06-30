from pathlib import Path

doc_path = Path(r"C:\Users\yoonjoo2026\.gemini\antigravity\brain\811ce81f-ff5c-4555-8462-fe94cbaa8ddd\.system_generated\steps\16\content.md")
if not doc_path.exists():
    print("Doc not found")
    sys.exit(0)

lines = doc_path.read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if '"body"' in line or '"method"' in line or 'dependsOn' in line:
        start = max(0, idx - 5)
        end = min(len(lines), idx + 10)
        print(f"--- Line {idx} ---")
        for j in range(start, end):
            print(f"{j}: {lines[j]}")
        print("\n")
