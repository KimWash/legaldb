from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import shutil
import statistics
import time
from typing import Iterator


@dataclass
class AdvancedPageResult:
    page_index: int
    text_length: int
    mean_confidence: float
    used_psm: int
    used_crop: bool
    status: str
    text_file: str


@dataclass
class AdvancedOcrResult:
    text: str
    mean_confidence: float
    failed_pages: list[int]
    total_pages: int
    run_dir: Path
    status: str


def _ensure_dirs(base: Path) -> dict[str, Path]:
    rendered = base / "rendered"
    processed = base / "processed"
    page_txt = base / "page_txt"
    for directory in [base, rendered, processed, page_txt]:
        directory.mkdir(parents=True, exist_ok=True)
    return {"base": base, "rendered": rendered, "processed": processed, "page_txt": page_txt}


def _get_pdf_page_count(pdf_path: Path) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(doc)
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _iter_pdf_pages(pdf_path: Path, dpi: int, max_dim_limit: int = 0) -> Iterator[tuple[int, object]]:
    """Lazily yield (page_no, bgr_image) one page at a time to avoid loading all pages into memory.

    doc.close() is guaranteed via try/finally even when the caller breaks early or
    an exception is raised, preventing "IO Operation on closed file" in multi-threaded use.
    Per-page objects are explicitly deleted to release memory promptly.
    """
    import pypdfium2 as pdfium
    import cv2
    import numpy as np

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            
            # Adaptive scale calculation: render at target max dimension directly
            if max_dim_limit > 0:
                orig_w, orig_h = page.get_size()
                orig_max = max(orig_w, orig_h)
                default_pixel_max = orig_max * (dpi / 72.0)
                if default_pixel_max > max_dim_limit:
                    scale = max_dim_limit / orig_max
                else:
                    scale = dpi / 72.0
            else:
                scale = dpi / 72.0
                
            render_pil = page.render(scale=scale).to_pil()
            pil_img = render_pil.convert("RGB")
            rgb = np.array(pil_img)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            yield (page_index + 1, bgr)
            
            # Explicitly close PIL images to release backend C-buffers immediately
            try:
                render_pil.close()
                pil_img.close()
            except Exception:
                pass
                
            # Explicitly release per-page objects to avoid memory accumulation
            del page, render_pil, pil_img, rgb, bgr
    finally:
        # Guaranteed cleanup even on break, timeout, or exception
        try:
            doc.close()
        except Exception:
            pass


def _iter_pdf_pages_queued(
    pdf_path: Path,
    dpi: int,
    max_dim_limit: int = 0,
    queue_size: int = 4
) -> Iterator[tuple[int, object]]:
    """Runs PDF rendering in a background thread using a Queue to overlap with OCR inference."""
    import queue
    import threading

    q = queue.Queue(maxsize=queue_size)

    def producer():
        try:
            for page_no, image in _iter_pdf_pages(pdf_path, dpi=dpi, max_dim_limit=max_dim_limit):
                q.put((page_no, image))
            q.put((None, None))  # Sentinel for EOF
        except Exception as exc:
            q.put((None, exc))

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    while True:
        page_no, item = q.get()
        if page_no is None:
            if isinstance(item, Exception):
                raise item
            break
        yield page_no, item


def _preprocess_image(image):
    import cv2
    import numpy as np

    # Minimize image copies by working in-place as much as possible
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    del image  # Release original image reference immediately

    cv2.convertScaleAbs(gray, dst=gray, alpha=1.6, beta=8)
    cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        12,
        dst=gray
    )
    
    denoised = cv2.fastNlMeansDenoising(gray, h=15, templateWindowSize=7, searchWindowSize=21)
    del gray  # Release gray buffer immediately

    sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    cv2.filter2D(denoised, -1, sharpen_kernel, dst=denoised)
    
    padded = cv2.copyMakeBorder(denoised, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    del denoised  # Release denoised buffer immediately
    
    return padded



