# Docling + Apple Vision OCR Convert Tool

Convert PDFs and page images using Docling and Apple Vision OCR (macOS).
Run commands from this directory with the existing `.venv`.

```bash

# Create a virtual environment.
python -m venv .venv

# Activate the virtual environment.
source .venv/bin/activate

# Install dependencies from requirements-docling.lock.txt.
pip install -r requirements-docling.lock.txt

# First run needs online access to download Docling layout/table models.
HF_HUB_OFFLINE=0 .venv/bin/python scripts/convert_sources_docling.py
.venv/bin/python scripts/finalize_docling.py

# Smoke test: first PDF page only, isolated in docling_preview/.
HF_HUB_OFFLINE=0 .venv/bin/python scripts/convert_sources_docling.py --max-pages 1
```

Inputs: PDF, JPG, JPEG, PNG, WEBP, TIF, TIFF in `sources/`.
Use `--only FILENAME` to select one file or `--limit N` to limit source files.
Source filenames must have unique stems. Full conversions go to `docling_output/`.
Successful results resume when the source checksum and output files match.
Failures return a nonzero exit code; partial conversions are treated as failures.

Both paths use Spanish/English full-page OCR. Images also receive orientation
normalization and independent Apple Vision OCR audit records. PDFs are passed
straight to Docling to retain page geometry; their `ocr/*.json` is an empty
placeholder, marked `raw_ocr_available: false` in metadata. Read PDF text and
bounding boxes from `documents/*.json`; the placeholder is not evidence of blank pages.
The finalizer validates exported document schemas and generates aggregate files;
it does not manually verify OCR accuracy or visually inspect pages.

Models are cached under `.cache-docling/huggingface` unless `HF_HOME` is set.
Use `HF_HUB_OFFLINE=1` only after the necessary models have been downloaded.
