# High Quality PDF OCR Tool

OCR pipeline for low-quality scanned PDFs.

## Features

1. Render each PDF page to `400dpi` PNG
2. Apply preprocessing: grayscale, contrast enhancement, threshold, denoise, sharpen, padding
3. OCR with Tesseract `-l eng --oem 1 --psm 6`
4. If text is too short or empty, retry with `--psm 11`
5. If still poor, auto-crop body region and OCR retry
6. Save per-page txt and merged txt
7. Save `summary.json` with confidence, text length, and failed pages

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python src/main.py ^
  --pdf "D:\\work\\2026_workspace\\Legal_DB\\법무DB(테스트)\\투자계약\\수단 대우아파트 PJT\\1. 계약서\\(계약)수정계약서(1993.4월).pdf" ^
  --out "./output_run"
```

Optional:

```bash
python src/main.py --pdf "<PDF_PATH>" --out "./output_run" --lang eng --min-len 40 --dpi 400
```

## Output

- `rendered/page_0001.png`
- `processed/page_0001_preprocessed.png`
- `processed/page_0001_cropped.png` (if crop retry used)
- `page_txt/page_0001.txt`
- `merged.txt`
- `summary.json`
