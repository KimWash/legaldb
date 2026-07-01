from __future__ import annotations

"""Intel-accelerated OCR backend (OpenVINO + RapidOCR).

Targets the Intel Ultra 7 155H: tries Arc GPU first, then NPU, then CPU.
Falls back gracefully (status='unavailable') when the optional dependency or a
hardware device is missing, so callers can drop back to the Tesseract pipeline.

Public API mirrors ocr_advanced so extractors can swap backends:
  - run_accelerated_ocr(pdf_path, temp_dir, config, timeout_seconds) -> AdvancedOcrResult
  - ocr_image_array(image, config) -> (text, mean_confidence_0_100)
"""

from dataclasses import asdict
import os
import sys

# OpenVINO 2026.x에서 openvino.runtime이 제거됨에 따른 하위 호환성 패치 (Mocking) 및 메모리 누수/디바이스 최적화
try:
    import openvino
    sys.modules["openvino.runtime"] = openvino
    
    # Core.compile_model을 몽키패치하여 CPU 메모리 누수를 방지하고 GPU 강제 실행 처리
    _orig_compile_model = openvino.Core.compile_model
    
    def _patched_compile_model(self, model, device_name=None, config=None, *args, **kwargs):
        if config is None:
            config = {}
        else:
            config = dict(config)
            
        try:
            available = self.available_devices
        except Exception:
            available = ["CPU"]
            
        # rapidocr-openvino의 CPU 하드코딩 우회 및 GPU 가속 강제 적용
        if device_name == "CPU" and "GPU" in available:
            device_name = "GPU"
            
        if device_name == "CPU":
            # CPU 구동 시 dynamic shapes로 인한 메모리 증식(캐시 리크) 방지
            config["CPU_RUNTIME_CACHE_CAPACITY"] = "0"
            
        return _orig_compile_model(self, model, device_name, config, *args, **kwargs)
        
    openvino.Core.compile_model = _patched_compile_model
except ImportError:
    pass

# Intel OpenVINO 가속 백엔드 CPU 스레드 과다 생성 방지 (컨텐션 완화)
if "OV_CPU_NUM_THREADS" not in os.environ:
    os.environ["OV_CPU_NUM_THREADS"] = "4"
if "OPENVINO_NUM_THREADS" not in os.environ:
    os.environ["OPENVINO_NUM_THREADS"] = "4"
if "OMP_NUM_THREADS" not in os.environ:
    os.environ["OMP_NUM_THREADS"] = "4"

from pathlib import Path
import hashlib
import json
import shutil
import statistics
import threading
import time

from ocr_advanced import (
    AdvancedOcrResult,
    AdvancedPageResult,
    _ensure_dirs,
    _get_pdf_page_count,
    _iter_pdf_pages,
    _iter_pdf_pages_queued,
    _preprocess_image,
)

# --- OpenVINO GPU/NPU Compilation & Model Caching Patch ---
try:
    import rapidocr_openvino.utils
    from openvino.runtime import Core
    import os
    
    _orig_init = rapidocr_openvino.utils.OpenVINOInferSession.__init__
    
    def _patched_init(self, config):
        core = Core()
        
        # Set cache directory to temp/ov_cache relative to project root
        project_root = Path(__file__).resolve().parents[1]
        cache_dir = project_root / "temp" / "ov_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        core.set_property({"CACHE_DIR": str(cache_dir)})
        
        self._verify_model(config["model_path"])
        model_onnx = core.read_model(config["model_path"])
        
        # Read device from environment hint
        device = os.environ.get("RAPIDOCR_OPENVINO_INFERENCE_DEVICE", "CPU").upper()
        
        # Match against available hardware devices
        available_devices = core.available_devices
        matched_device = "CPU"
        for d in available_devices:
            if device in d.upper():
                matched_device = d
                break
                
        if matched_device == "CPU":
            cpu_nums = os.cpu_count() or 4
            infer_num_threads = config.get("inference_num_threads", -1)
            if infer_num_threads == -1:
                infer_num_threads = max(1, cpu_nums // 4)
            core.set_property("CPU", {"INFERENCE_NUM_THREADS": str(infer_num_threads)})
            core.set_property("CPU", {"PERFORMANCE_HINT": "THROUGHPUT"})
                
        compile_model = core.compile_model(model=model_onnx, device_name=matched_device)
        self.session = compile_model.create_infer_request()
        
    rapidocr_openvino.utils.OpenVINOInferSession.__init__ = _patched_init
except Exception:
    pass
# -----------------------------------------------------------

# 글로벌 공유 엔진 및 추론 동기화 락
_ENGINE_LOCK = threading.Lock()  # 엔진 로드 및 컴파일 동시성 제어용
_INFERENCE_LOCK = threading.Lock()  # GPU 추론 세션 세이프 락
_SHARED_ENGINE_STATE = {"engine": None, "device": "", "backend": ""}


def _verify_openvino_gpu() -> tuple[list[str], bool]:
    """Check available OpenVINO devices and verify if GPU is active."""
    try:
        from openvino.runtime import Core
        core = Core()
        devices = core.available_devices
        has_gpu = any("GPU" in d.upper() for d in devices)
        return devices, has_gpu
    except Exception:
        return [], False


def _device_priority(config: dict) -> list[str]:
    raw = config.get("device_priority") or ["GPU", "NPU", "CPU"]
    out: list[str] = []
    for d in raw:
        d = str(d).strip().upper()
        if d and d not in out:
            out.append(d)
    return out or ["CPU"]


def _try_build_rapidocr_openvino(device_priority: list[str]):
    """Older `rapidocr_openvino` package. Device is best-effort via env hint."""
    import os
    try:
        from rapidocr_openvino import RapidOCR
    except Exception:
        return None
    for device in device_priority:
        try:
            # Some builds honor this env hint for the OpenVINO inference device.
            os.environ["RAPIDOCR_OPENVINO_INFERENCE_DEVICE"] = device
            engine = RapidOCR()
            return engine, device, "rapidocr_openvino"
        except Exception:
            continue
    return None


def _try_build_rapidocr(device_priority: list[str], lang: str):
    """Newer unified `rapidocr` (>=2.0) with explicit OpenVINO engine + device."""
    try:
        from rapidocr import RapidOCR, EngineType  # type: ignore
    except Exception:
        return None
    for device in device_priority:
        try:
            params = {
                "Det.engine_type": EngineType.OPENVINO,
                "Cls.engine_type": EngineType.OPENVINO,
                "Rec.engine_type": EngineType.OPENVINO,
                "EngineConfig.openvino.inference_device": device,
            }
            engine = RapidOCR(params=params)
            return engine, device, "rapidocr"
        except Exception:
            continue
    return None


def _get_engine(config: dict):
    """Return (engine, device, backend) using a process-wide shared singleton engine."""
    global _SHARED_ENGINE_STATE
    if _SHARED_ENGINE_STATE["engine"] is None:
        # 모델 로딩 시점에 여러 스레드가 동시에 생성하여 하드웨어 부하가 집중되는 것을 방지하기 위해 빌드만 락 보호
        with _ENGINE_LOCK:
            if _SHARED_ENGINE_STATE["engine"] is None:
                device_priority = _device_priority(config)
                lang = str(config.get("lang", "korean"))
                built = _try_build_rapidocr(device_priority, lang) or _try_build_rapidocr_openvino(device_priority)
                tid = threading.get_ident()
                if built is None:
                    print(f"[ocr_accelerated][Thread-{tid}] RapidOCR/OpenVINO unavailable - falling back to Tesseract pipeline.")
                    _SHARED_ENGINE_STATE = {"engine": None, "device": "", "backend": "unavailable"}
                else:
                    engine, device, backend = built
                    devices, has_gpu = _verify_openvino_gpu()
                    print(f"[ocr_accelerated][Thread-{tid}] Shared singleton engine ready backend={backend} requested_device={device}")
                    print(f"[ocr_accelerated][Thread-{tid}] OpenVINO devices: {devices} (Has GPU: {has_gpu})")
                    _SHARED_ENGINE_STATE = {"engine": engine, "device": device, "backend": backend}
    st = _SHARED_ENGINE_STATE
    return st["engine"], st["device"], st["backend"]


def is_available(config: dict) -> bool:
    engine, _device, backend = _get_engine(config)
    return engine is not None and backend != "unavailable"


def _run_engine_on_image(engine, image, max_dim_limit: int = 1536) -> tuple[str, float]:
    """Run RapidOCR on a BGR/RGB numpy image. Returns (text, mean_conf_0_100)."""
    # Removed global _INFERENCE_LOCK to allow concurrent inference on thread-local engines
    import cv2
    if image is not None and hasattr(image, "shape"):
        h, w = image.shape[:2]
        max_dim = max(h, w)
        if max_dim_limit > 0 and max_dim > max_dim_limit:
            scale = max_dim_limit / max_dim
            new_w = int(w * scale)
            new_h = int(h * scale)
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
            tid = threading.get_ident()
            print(f"[ocr_accelerated][Thread-{tid}] Image resized from {w}x{h} to {new_w}x{new_h} (max_dim_limit={max_dim_limit})")

    with _INFERENCE_LOCK:
        output = engine(image)
    # rapidocr_openvino: returns (result, elapse); result = [[box, text, score], ...] or None
    # rapidocr 2.x: returns an object with .txts / .scores
    texts: list[str] = []
    scores: list[float] = []
    if isinstance(output, tuple):
        result = output[0]
        if result:
            for line in result:
                try:
                    texts.append(str(line[1]))
                    scores.append(float(line[2]))
                except Exception:
                    continue
    else:
        # object-style result (rapidocr 2.x)
        txts = getattr(output, "txts", None)
        scs = getattr(output, "scores", None)
        if txts:
            for i, t in enumerate(txts):
                texts.append(str(t))
                try:
                    scores.append(float(scs[i]))
                except Exception:
                    pass
    merged = " ".join(t.strip() for t in texts if t and t.strip()).strip()
    mean_conf = (statistics.mean(scores) * 100.0) if scores else 0.0
    return merged, mean_conf


def ocr_image_array(image, config: dict) -> tuple[str, float] | None:
    """OCR a single preprocessed image array. Returns (text, conf_0_100) or None if backend unavailable."""
    engine, _device, backend = _get_engine(config)
    if engine is None or backend == "unavailable":
        return None
    try:
        limit = int(config.get("max_dim", 1536)) if config else 1536
        return _run_engine_on_image(engine, image, max_dim_limit=limit)
    except Exception as exc:
        print(f"[ocr_accelerated] image OCR failed ({exc}); will fall back.")
        return None


def run_accelerated_ocr(pdf_path: Path, temp_dir: Path, config: dict, timeout_seconds: int = 0) -> AdvancedOcrResult:
    """OCR a PDF page-by-page with the OpenVINO/RapidOCR backend.

    Returns AdvancedOcrResult (status='unavailable' if the backend cannot be used,
    so the caller can fall back to Tesseract).
    """
    run_key = hashlib.sha1(str(pdf_path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    run_dir = temp_dir / "ocr_runs" / f"accel_{run_key}"

    engine, device, backend = _get_engine(config)
    if engine is None or backend == "unavailable":
        return AdvancedOcrResult(
            text="", mean_confidence=0.0, failed_pages=[], total_pages=0,
            run_dir=run_dir, status="unavailable",
        )

    dirs = _ensure_dirs(run_dir)
    dpi = int(config.get("dpi", 300))
    min_len = int(config.get("min_len", 40))
    preprocess = bool(config.get("preprocess", False))
    max_dim_limit = int(config.get("max_dim", 1536))

    try:
        total_pages = _get_pdf_page_count(pdf_path)
    except Exception as exc:
        return AdvancedOcrResult(
            text="", mean_confidence=0.0, failed_pages=[], total_pages=0,
            run_dir=run_dir, status=f"render_error:{exc}",
        )

    page_results: list[AdvancedPageResult] = []
    failed_pages: list[int] = []
    merged_parts: list[str] = []
    all_conf: list[float] = []
    timed_out = False
    start_time = time.monotonic()

    for page_no, image in _iter_pdf_pages_queued(pdf_path, dpi=dpi, max_dim_limit=max_dim_limit):
        if timeout_seconds > 0 and time.monotonic() - start_time > timeout_seconds:
            timed_out = True
            break
        img = _preprocess_image(image) if preprocess else image
        try:
            text, conf = _run_engine_on_image(engine, img, max_dim_limit=max_dim_limit)
        except Exception as exc:
            text, conf = "", 0.0
            print(f"[ocr_accelerated] page {page_no} OCR failed: {exc}")

        normalized = text.strip() or "__NO_TEXT__"
        text_len = 0 if normalized == "__NO_TEXT__" else len(normalized)
        txt_path = dirs["page_txt"] / f"page_{page_no:04d}.txt"
        txt_path.write_text(normalized, encoding="utf-8")

        # Explicitly release per-page image objects to keep memory low
        try:
            del image, img
        except Exception:
            pass

        if normalized == "__NO_TEXT__" or text_len < min_len:
            status = "failed"
            failed_pages.append(page_no)
        else:
            status = "success"
            merged_parts.append(f"===== PAGE {page_no} =====\n{normalized}")

        all_conf.append(conf)
        page_results.append(
            AdvancedPageResult(
                page_index=page_no, text_length=text_len,
                mean_confidence=round(conf, 3), used_psm=0, used_crop=False,
                status=status, text_file=str(txt_path),
            )
        )

    merged_text = "\n\n".join(merged_parts).strip() if merged_parts else "__NO_TEXT__"
    (dirs["base"] / "merged.txt").write_text(merged_text, encoding="utf-8")
    mean_conf = round(statistics.mean(all_conf), 3) if all_conf else 0.0
    if timed_out:
        final_status = "timeout_partial" if merged_text != "__NO_TEXT__" else "timeout_no_text"
    else:
        final_status = "success" if merged_text != "__NO_TEXT__" else "no_text"

    # ── 임시 파일 정리 (rendered/processed/page_txt 삭제) ─────────────────────────
    # cleanup_after_ocr=True(기본)이면 OCR 완료 후 대용량 임시 폴더 삭제
    if bool(config.get("cleanup_after_ocr", True)):
        for _subdir in ("rendered", "processed", "page_txt"):
            try:
                shutil.rmtree(dirs[_subdir], ignore_errors=True)
            except Exception:
                pass
        print(f"[ocr_accelerated] 임시 이미지 정리 완료: {run_dir}")

    summary = {
        "pdf_path": str(pdf_path),
        "backend": backend,
        "device": device,
        "total_pages": total_pages,
        "processed_pages": len(page_results),
        "timed_out": timed_out,
        "success_pages": sum(1 for it in page_results if it.status == "success"),
        "failed_pages": failed_pages,
        "mean_confidence": mean_conf,
        "settings": {"dpi": dpi, "min_len": min_len, "preprocess": preprocess},
        "pages": [asdict(it) for it in page_results],
    }
    (dirs["base"] / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return AdvancedOcrResult(
        text=merged_text if merged_text != "__NO_TEXT__" else "",
        mean_confidence=mean_conf,
        failed_pages=failed_pages,
        total_pages=total_pages,
        run_dir=run_dir,
        status=final_status,
    )
