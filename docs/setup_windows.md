# Windows Setup

## 1. Open PowerShell

```powershell
cd D:\Stage_mr_f\invoice-ocr-erp
```

## 2. Create or activate the virtual environment

If `.venv` already exists:

```powershell
.\.venv\Scripts\Activate.ps1
```

If it does not exist:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

## 3. OCR Dependencies

The project can use PaddleOCR and Tesseract. At least one OCR path must work for real image extraction.

Check benchmark OCR environment:

```powershell
python scripts/benchmark_multi_datasets.py --check-env
```

## 4. Start the App

```powershell
python run.py
```

Open:

```text
http://127.0.0.1:8000/
```

Swagger remains available at:

```text
http://127.0.0.1:8000/docs
```

## 5. Run Tests

```powershell
python -m pytest
python -m compileall -q app scripts tests
```

