# Windows setup

## Requirements

- Windows 10/11 and Python 3.11 or newer.
- Git for checkout/update workflows.
- PaddleOCR dependencies are installed from `requirements.txt`. First OCR use may initialize/download model assets, so allow time and network access as required by the configured engine.
- Tesseract is optional. Install it separately and configure its executable/languages if you want to use the fallback path.

## Create an environment and run

From the repository directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8000/`. The API schema is at `http://127.0.0.1:8000/docs` and the health check is `http://127.0.0.1:8000/health`.

If PowerShell blocks local activation, either use the venv interpreter directly (`.\.venv\Scripts\python.exe`) or adjust the execution policy for the current user according to your organization’s policy. Do not weaken a managed machine’s policy without approval.

## Development and optional models

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Optional model experiments have a separate dependency file:

```powershell
python -m pip install -r requirements-ml.txt
```

They are not needed for the default application path. Copy `.env.example` to `.env` only when local settings need to differ from defaults; keep `.env` and credentials out of Git.

## Local data and outputs

Use an external/private dataset directory for client files. Keep client PDFs, OCR evidence, and verified labels under ignored local storage such as `local_data/` or the ignored `dossier_ground_truth/` directory. Generated outputs and caches should remain local; check `git status --short` before every commit.

For the benchmark modes and dataset-root options, see [Tiered evaluation](benchmarks/tiered-evaluation.md) and [Multi-dataset benchmark](benchmarks/multi-dataset.md).
