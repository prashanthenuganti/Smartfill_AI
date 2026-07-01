# SmartFill AI — Milestone 1

AI-powered OCR engine for Indian Internet Centers.
Extracts structured data from Aadhaar and PAN cards in 2–5 seconds.

---

## What this does

An Internet Center operator uploads a customer's Aadhaar card and/or PAN card
via a Chrome Extension popup. The backend runs OCR, extracts every field, and
returns structured JSON with per-field confidence scores. Fields the operator
should verify before submitting are flagged automatically.

**Milestone 1 scope:** OCR extraction only. No browser autofill (Milestone 2).

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| Tesseract OCR | 5.x | See install instructions below |
| Google Chrome | any recent | For the extension |
| pip | any | Comes with Python |

### Install Tesseract

**Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-hin
```

**macOS**
```bash
brew install tesseract
```

**Windows**
Download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki).
Install to `C:\Program Files\Tesseract-OCR\` and add to PATH.

Verify: `tesseract --version` should print `tesseract 5.x.x`.

---

## Quick start

### 1. Clone and enter the project

```bash
git clone <your-repo-url> smartfill-ai
cd smartfill-ai
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set `TESSERACT_CMD` to the path shown by `which tesseract`
(Linux/macOS) or the full Windows path.

```env
# Linux / macOS (default)
TESSERACT_CMD=/usr/bin/tesseract

# macOS Homebrew
TESSERACT_CMD=/opt/homebrew/bin/tesseract

# Windows
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

### 5. Start the backend

```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

You should see:

```
SmartFill AI starting up
  environment : development
  host        : 127.0.0.1:8000
Pipeline pre-warmed and ready
```

Open `http://127.0.0.1:8000/docs` in your browser to see the interactive API docs.

### 6. Load the Chrome Extension

1. Open Chrome and go to `chrome://extensions/`
2. Enable **Developer mode** (toggle, top right)
3. Click **Load unpacked**
4. Select the `chrome-extension/` folder inside the project
5. The SmartFill AI icon appears in your toolbar

Click the icon — the popup opens. The status indicator should show **Backend online**.

---

## Project structure

```
smartfill-ai/
│
├── backend/
│   └── app/
│       ├── api/v1/
│       │   └── routes.py          ← POST /api/v1/process endpoint
│       ├── core/
│       │   ├── config.py          ← All settings (reads .env)
│       │   ├── exceptions.py      ← Custom exception types
│       │   └── logging.py         ← PII-safe structured logging
│       ├── schemas/
│       │   ├── documents.py       ← DocumentType, UploadedFile
│       │   ├── extraction.py      ← AadhaarExtraction, PANExtraction, response types
│       │   └── errors.py          ← Standard error envelope
│       ├── services/
│       │   ├── ocr/
│       │   │   ├── engine.py      ← Tesseract OCR + two-pass retry
│       │   │   ├── preprocessor.py← Image preprocessing pipeline
│       │   │   └── pdf_converter.py← PDF → images (PyMuPDF)
│       │   ├── parsers/
│       │   │   ├── base.py        ← BaseDocumentParser interface
│       │   │   ├── aadhaar_parser.py
│       │   │   └── pan_parser.py
│       │   └── pipeline/
│       │       └── orchestrator.py← Wires OCR + parsers, handles concurrency
│       ├── utils/
│       │   ├── file_validator.py  ← Upload validation + magic byte check
│       │   └── text_normalizer.py ← DOB, Aadhaar, PAN, name normalisation
│       └── main.py                ← FastAPI app factory + lifespan
│
├── chrome-extension/
│   ├── manifest.json              ← MV3 manifest
│   ├── popup.html                 ← Operator UI
│   ├── popup.css
│   ├── popup.js                   ← Upload, API call, render results
│   ├── background.js              ← Service worker (placeholder for M2)
│   └── content.js                 ← Content script (placeholder for M2)
│
├── tests/
│   ├── unit/                      ← Fast tests, no I/O
│   │   ├── test_schemas.py
│   │   ├── test_text_normalizer.py
│   │   ├── test_file_validator.py
│   │   ├── test_preprocessor.py
│   │   ├── test_ocr_engine.py
│   │   ├── test_parsers.py
│   │   └── test_pipeline.py
│   └── integration/
│       └── test_api.py            ← Full HTTP tests (no real OCR needed)
│
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Running tests

```bash
# All tests
PYTHONPATH=. python3 -m pytest tests/ -v

# Unit tests only (fast, ~18s)
PYTHONPATH=. python3 -m pytest tests/unit/ -v

# Integration tests only
PYTHONPATH=. python3 -m pytest tests/integration/ -v

# Single module
PYTHONPATH=. python3 -m pytest tests/unit/test_parsers.py -v
```

Expected: **176 passed** across 7 test files.

---

## API reference

### `GET /api/v1/health`

Liveness check. Used by the extension to verify the backend is reachable.

```json
{ "status": "ok", "service": "SmartFill AI", "version": "1.0.0" }
```

---

### `POST /api/v1/process`

Extract structured data from uploaded documents.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `aadhaar_file` | file | optional | Aadhaar card (PDF/PNG/JPG) |
| `pan_file` | file | optional | PAN card (PDF/PNG/JPG) |

At least one file must be provided.

**Response:** `200 OK`

```json
{
  "status": "success",
  "aadhaar": {
    "name":           { "value": "Ravi Kumar",    "confidence": 95.0, "needs_review": false },
    "father_name":    { "value": "Rajesh Kumar",  "confidence": 92.0, "needs_review": false },
    "dob":            { "value": "1990-05-15",    "confidence": 88.0, "needs_review": false },
    "gender":         { "value": "Male",          "confidence": 97.0, "needs_review": false },
    "aadhaar_number": { "value": "123456789012",  "confidence": 99.0, "needs_review": false },
    "year_of_birth":  { "value": "1990",          "confidence": 75.0, "needs_review": false }
  },
  "pan": {
    "name":        { "value": "RAVI KUMAR",   "confidence": 91.0, "needs_review": false },
    "father_name": { "value": "RAJESH KUMAR", "confidence": 89.0, "needs_review": false },
    "pan_number":  { "value": "ABCDE1234F",   "confidence": 99.0, "needs_review": false }
  },
  "has_errors": false,
  "errors": [],
  "fields_needing_review": [],
  "processing_time_ms": 2340.5
}
```

**Confidence scores** are 0–100. Fields with `needs_review: true` should be
verified by the operator before submitting the application.

**Error response** (all errors use this shape):

```json
{
  "status": "error",
  "error_code": "FILE_TOO_LARGE",
  "message": "File exceeds the 10 MB limit.",
  "details": { "limit_mb": 10 }
}
```

| HTTP status | `error_code` | Cause |
|---|---|---|
| 400 | `NO_FILE_PROVIDED` | No files in the request |
| 400 | `UNSUPPORTED_FILE_TYPE` | Extension not in PDF/PNG/JPG/JPEG |
| 400 | `CORRUPTED_FILE` | File bytes don't match declared extension |
| 413 | `FILE_TOO_LARGE` | File exceeds `MAX_FILE_SIZE_MB` |
| 500 | `INTERNAL_ERROR` | Unexpected server error |

---

## Configuration reference

All settings live in `.env`. Defaults are in `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | Server bind address |
| `APP_PORT` | `8000` | Server port |
| `APP_ENV` | `development` | `development` or `production` |
| `TESSERACT_CMD` | `/usr/bin/tesseract` | Path to Tesseract binary |
| `OCR_CONFIDENCE_THRESHOLD` | `60` | Below this → retry with aggressive preprocessing |
| `MAX_FILE_SIZE_MB` | `10` | Maximum upload size per file |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS origins (set to Chrome Extension ID in production) |

---

## How OCR works

```
Uploaded file
     │
     ▼
File Validator         ← magic byte check, size limit, extension allow-list
     │
     ▼
PDF Converter          ← PyMuPDF renders PDF pages at 300 DPI (images skip this)
     │
     ▼
Image Preprocessor     ← resize → greyscale → denoise → deskew →
     │                   adaptive threshold → morphological cleanup
     ▼
Tesseract OCR          ← PSM 6 (uniform text block), OEM 3 (LSTM engine)
     │                   returns text + per-word confidence scores
     ▼
Confidence check       ← if avg confidence < threshold → retry with
     │                   aggressive preprocessing (stronger denoise + dilate)
     ▼
Document Parser        ← label-first extraction, regex fallback,
     │                   normalise (DOB → YYYY-MM-DD, Aadhaar → 12 digits)
     ▼
ExtractionField        ← value + confidence + needs_review flag per field
     │
     ▼
ProcessingResponse     ← unified JSON response with all documents
```

---

## Privacy and data handling

- Uploaded files are written to `TMP_DIR` (default `/tmp/smartfill`) using a
  random hash filename — the original filename never touches the filesystem.
- Temp files are deleted in a `finally` block after processing completes,
  whether or not OCR succeeded.
- Aadhaar numbers, PAN numbers, and personal information are **never logged**.
  The logging layer has a `PIIFilter` that scrubs any log message containing
  sensitive field names before it reaches stdout.
- No customer data is stored in any database. Each request is fully stateless.

---

## Adding a new document type (Milestone 2 guide)

1. Add the new type to `DocumentType` enum in `schemas/documents.py`:
   ```python
   PASSPORT = "passport"
   ```

2. Create `services/parsers/passport_parser.py` implementing `BaseDocumentParser`.

3. Register it in the pipeline (`services/pipeline/orchestrator.py`):
   ```python
   DocumentType.PASSPORT: PassportParser(),
   ```

4. Add the extraction schema to `schemas/extraction.py`.

5. Write tests in `tests/unit/test_parsers.py`.

No other files need to change.

---

## Troubleshooting

**`tesseract: command not found`**
Set `TESSERACT_CMD` in `.env` to the full path. Find it with `which tesseract`.

**`ModuleNotFoundError`**
Always run with `PYTHONPATH=.` prefix:
```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload
```

**Extension shows "Backend offline"**
Make sure the backend is running on port 8000 and the extension has
`host_permissions` for `http://127.0.0.1:8000/*` in `manifest.json`.

**Low confidence on real Aadhaar cards**
Phone photos taken at an angle produce low-confidence results. Scan flat,
or increase `OCR_CONFIDENCE_THRESHOLD` to 70 to force the aggressive
preprocessing retry more aggressively.

**`pdf2image` errors**
`pdf2image` requires `poppler`. Install with:
```bash
# Ubuntu
sudo apt-get install poppler-utils
# macOS
brew install poppler
```
PyMuPDF (the primary PDF converter) does not require poppler and is preferred.

---

## Tech stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.111 + Uvicorn |
| Data validation | Pydantic v2 |
| OCR engine | Tesseract 5.x via pytesseract |
| Image processing | OpenCV (headless) + Pillow |
| PDF conversion | PyMuPDF (primary) + pdf2image (fallback) |
| Testing | pytest + FastAPI TestClient + httpx |
| Extension | Chrome MV3, Vanilla JS, no build step |

---

## Milestone roadmap

| Milestone | Description | Status |
|---|---|---|
| **1** | OCR engine — Aadhaar + PAN extraction | ✅ Complete |
| 2 | AI field mapping + browser form autofill | Planned |
| 3 | Multi-document support (Passport, DL, Resume) | Planned |
| 4 | Multi-step form handling | Planned |
| 5 | Document upload automation | Planned |
| 6 | SaaS subscription + operator dashboard | Planned |
