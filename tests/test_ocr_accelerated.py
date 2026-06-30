import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocr_accelerated import _run_engine_on_image


def test_resize_large_image():
    # Create a mock engine that does nothing but record input image shape
    called_shape = None

    def mock_engine(img):
        nonlocal called_shape
        called_shape = img.shape
        # Return a structure matching what RapidOCR expects/returns
        return ([], 0.0)

    # 1. Image is larger than limit (2000x3000) with max_dim_limit = 1000
    large_image = np.zeros((2000, 3000, 3), dtype=np.uint8)
    _run_engine_on_image(mock_engine, large_image, max_dim_limit=1000)

    assert called_shape is not None
    # Aspect ratio is 3:2, max dimension should be 1000, so new shape should be (666, 1000, 3)
    assert called_shape[1] == 1000
    assert called_shape[0] == 666

    # 2. Image is smaller than limit (400x600) with max_dim_limit = 1000
    small_image = np.zeros((400, 600, 3), dtype=np.uint8)
    called_shape = None
    _run_engine_on_image(mock_engine, small_image, max_dim_limit=1000)

    assert called_shape is not None
    assert called_shape == (400, 600, 3)

    # 3. Limit is disabled (max_dim_limit = 0)
    called_shape = None
    _run_engine_on_image(mock_engine, large_image, max_dim_limit=0)
    assert called_shape == (2000, 3000, 3)

    print("OCR accelerated resize tests passed successfully!")


if __name__ == "__main__":
    test_resize_large_image()
