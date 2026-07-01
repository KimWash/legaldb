from __future__ import annotations

import sys
from pathlib import Path
from models import ExtractionResult
from com_lock import ppt_lock

import sys
import subprocess
import json
from pathlib import Path
from models import ExtractionResult
from com_lock import ppt_lock

def extract_ppt(path: str | Path, max_chars: int = 8000) -> ExtractionResult:
    path = Path(path)
    if sys.platform != "win32":
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=["Legacy .ppt extraction via COM is only supported on Windows."],
        )
        
    cli_path = Path(__file__).resolve().parent / "com_extractor_cli.py"
    
    try:
        with ppt_lock:
            res = subprocess.run([
                sys.executable,
                str(cli_path),
                "--type", "ppt",
                "--file", str(path.resolve()),
                "--max-chars", str(max_chars)
            ], capture_output=True, text=True, timeout=15)
            
        if res.returncode != 0:
            raise Exception(f"Subprocess returned non-zero code {res.returncode}. Stderr: {res.stderr.strip()}")
            
        payload = json.loads(res.stdout.strip())
        if payload.get("status") == "success":
            raw_text = payload.get("text", "")
            combined = raw_text.strip()
            slide_count = payload.get("page_count", 0)
            if not combined:
                return ExtractionResult(
                    file_type="ppt",
                    extraction_status="empty_text",
                    notes=["No text extracted from PPT via COM subprocess."],
                )
            return ExtractionResult(
                file_type="ppt",
                extraction_status="success",
                extracted_text=combined,
                text_excerpt=combined[:max_chars],
                page_count=slide_count,
                notes=[f"PPT text extracted via COM subprocess. {slide_count} slides."],
            )
        else:
            raise Exception(payload.get("error", "Unknown error in COM subprocess"))
            
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/f", "/im", "POWERPNT.EXE"], capture_output=True)
        except Exception:
            pass
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=["Legacy .ppt extraction timed out (possibly DRM protected or PowerPoint dialog hang)."],
        )
    except Exception as exc:
        return ExtractionResult(
            file_type="ppt",
            extraction_status="manual_review_required",
            notes=[f"Legacy .ppt extraction failed (possibly DRM protected): {exc}"],
        )

