from __future__ import annotations

import sys
from pathlib import Path
from models import ExtractionResult
from com_lock import word_lock

import sys
import subprocess
import json
from pathlib import Path
from models import ExtractionResult
from com_lock import word_lock

def extract_doc(path: str | Path, max_chars: int = 8000) -> ExtractionResult:
    path = Path(path)
    if sys.platform != "win32":
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=["Legacy .doc extraction via COM is only supported on Windows."],
        )
        
    cli_path = Path(__file__).resolve().parent / "com_extractor_cli.py"
    
    try:
        with word_lock:
            res = subprocess.run([
                sys.executable,
                str(cli_path),
                "--type", "doc",
                "--file", str(path.resolve()),
                "--max-chars", str(max_chars)
            ], capture_output=True, text=True, timeout=15)
            
        if res.returncode != 0:
            raise Exception(f"Subprocess returned non-zero code {res.returncode}. Stderr: {res.stderr.strip()}")
            
        payload = json.loads(res.stdout.strip())
        if payload.get("status") == "success":
            raw_text = payload.get("text", "")
            combined = raw_text.replace("\r", "\n").strip()
            if not combined:
                return ExtractionResult(
                    file_type="doc",
                    extraction_status="empty_text",
                    notes=["No text extracted from DOC via COM subprocess."],
                )
            return ExtractionResult(
                file_type="doc",
                extraction_status="success",
                extracted_text=combined,
                text_excerpt=combined[:max_chars],
                notes=["DOC text extracted via COM subprocess."],
            )
        else:
            raise Exception(payload.get("error", "Unknown error in COM subprocess"))
            
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/f", "/im", "WINWORD.EXE"], capture_output=True)
        except Exception:
            pass
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=["Legacy .doc extraction timed out (possibly DRM protected or Word dialog hang)."],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="doc",
            extraction_status="manual_review_required",
            notes=[f"Legacy .doc extraction failed (possibly DRM protected): {exc}"],
        )

