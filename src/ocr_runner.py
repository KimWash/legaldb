from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def run_ocrmypdf(
    source_pdf: Path,
    output_pdf: Path,
    language: str,
    skip_text: bool = True,
    timeout_seconds: int = 300,
) -> tuple[bool, str]:
    if shutil.which("ocrmypdf") is None:
        return False, "ocrmypdf_not_installed"
    command = ["ocrmypdf", "-l", language]
    if skip_text:
        command.append("--skip-text")
    command.extend([str(source_pdf), str(output_pdf)])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"ocr_timeout:{timeout_seconds}s"
    except Exception as exc:
        return False, f"ocr_exception:{exc}"
    if completed.returncode != 0:
        return False, f"ocr_failed:{(completed.stderr.strip() or completed.stdout.strip())}"
    return True, "success"
