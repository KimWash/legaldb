# Legal DB Rename Project

Local-folder based PoC for legal document filename standardization.

## Install

```bash
pip install -r requirements.txt
```

Optional runtime dependencies:

- OCR: `Tesseract`, `Ghostscript`, `ocrmypdf`
- Local LLM: `Ollama` + `qwen3.5:latest`
- GPT API: set `OPENAI_API_KEY` in `.env` and use `llm.provider: "openai"` in `config.yaml`

Create `.env` from template:

```bash
copy .env.example .env
```

Then set your key:

```text
OPENAI_API_KEY=sk-...
```

Advanced OCR for low-quality scanned PDFs is built into `extractor_pdf.py`:

- Render pages at `400 DPI`
- Preprocess with grayscale, contrast, threshold, denoise, sharpen, padding
- OCR with `--oem 1 --psm 6`, then retry `--psm 11`
- If still poor, auto-crop body region and retry
- Save per-page text, merged text, and `summary.json` under `temp/ocr_runs/...`

Naming behavior:

- Files are read first (content-based extraction), filename is only a helper signal.
- `extracted_summary` is generated and written to review Excel.
- `extracted_document_title` is generated from summary and used in standard filename.
- Document title is normalized to max 10 characters.

## Run

Analyze and create review workbook:

```bash
python src/main.py --mode analyze
```

Apply approved rename:

```bash
python src/main.py --mode rename --review-file ./output/review/rename_review_YYYYMMDD_HHMMSS.xlsx
```

Rollback:

```bash
python src/main.py --mode rollback --rollback-file ./output/rollback/rollback_mapping_YYYYMMDD_HHMMSS.json
```

## Speed Tips

Fast draft pass (disable OCR + LLM):

```bash
python src/main.py --mode analyze --fast
```

Parallel workers:

```bash
python src/main.py --mode analyze --workers 6
```

Low-load tuning (recommended for office PC use):

```bash
python src/main.py --mode analyze --workers 4 --ocr-workers 1 --llm-workers 2
```

Process subset first (example: first 100 files):

```bash
python src/main.py --mode analyze --max-files 100
```

Trim LLM input length:

```bash
python src/main.py --mode analyze --llm-excerpt-chars 1500
```

Progress log interval:

```bash
python src/main.py --mode analyze --progress-every 5
```

Cache:

- Analysis cache is stored at `temp/analysis_cache.json`.
- Re-running with unchanged files reuses extracted text and prior LLM metadata for faster throughput.
