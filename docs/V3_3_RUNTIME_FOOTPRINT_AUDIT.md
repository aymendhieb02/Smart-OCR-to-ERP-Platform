# v3.3 Runtime Footprint Audit

Status: **candidate is materially smaller and passed the measured parity
checks, but v3.3 is not released**. The clean deployment-export pytest run did
not terminate with a summary/exit code, so the mandatory test gate remains
incomplete. Keep v3.2 in production until that gate is resolved and rerun.

This document contains aggregate measurements only. Client PDFs, OCR response
JSON, cache entries, and ground truth remain local under ignored paths; none of
their values are reproduced here.

## Scope and immutable baseline

- Source main and `origin/main` both started at `1eea6f63be2543947ff99accb21f16ecf6268429`.
- Annotated `v3.2` tag object: `d97a94243b16d902266fe72f8620ca1415e03006`.
- Audit branch: `feature/v3.3-runtime-footprint`.
- Production rollback image `ocr-ruspina:3.2` was not modified: image ID
  `sha256:c5f6bef2892c83110141a61d658135e2c2651d8ee6157da6bb90787ecf3d22bb`,
  Linux/amd64, 1,932,919,634 bytes, 11 layers, `appuser` UID 10001, one
  Uvicorn worker.
- The existing v3.2 TAR is 1,962,718,720 bytes. Its recorded and recomputed
  SHA-256 is
  `45C5D5DCAA97035A1092993B4E366D13922753EC84373BECF77A1DF2E68AB42F`.
- Existing source-only untracked paths `docs/vllm_environment_audit.md` and
  `dossier_ground_truth/` were left untouched.

The production image continues to use Python 3.11, CPU-only PaddlePaddle
3.3.1, PaddleOCR 3.7.0, PP-OCRv4 mobile detection/recognition models, Tesseract
5.3.0 with English/French data, and a single worker (required for in-memory
routing sessions).

## What occupies v3.2

Container filesystem measurements (`du -sb`):

| Path | Bytes | Notes |
|---|---:|---|
| `/opt/venv` | 1,544,690,289 | Dominant runtime footprint |
| `/opt/venv/lib/python3.11/site-packages` | 1,544,690,289 | Same venv payload measured directly |
| `/usr` | 381,271,323 | Debian/Python system files and OCR runtime libraries/data |
| `/opt/ocr-models` | 12,769,241 | Two required mobile model directories, eight files |
| `/app` | 898,984 | Application plus runtime directories/requirements |
| `/tmp` | 4 KiB | No material temporary cache |

Directory totals overlap their parent directories and should not be summed as
independent image layers. The image total is the authoritative image-size
measurement.

### Largest Python distributions in v3.2

Sizes below sum the files listed by each distribution's installed metadata;
distributions may claim overlapping `cv2` paths, so this ranking is not an
additive filesystem total.

| Distribution | Version | Recorded file bytes | Classification |
|---|---:|---:|---|
| paddlepaddle | 3.3.1 | 742,739,968 | REQUIRED |
| opencv-contrib-python | 4.10.0.84 | 188,790,240 | REQUIRED by PaddleX OCR-core extra |
| opencv-python-headless | 4.13.0.92 | 173,558,035 | REDUNDANT in this pinned export |
| numpy | 2.3.5 | 72,272,392 | REQUIRED |
| pandas | 3.0.6 | 71,316,879 | REQUIRED by PaddleX base dependency metadata |
| PyMuPDF | 1.27.2.3 | 64,066,504 | REQUIRED for PDF page rendering |
| modelscope | 1.40.1 | 53,294,824 | REQUIRED by PaddleX dependency metadata; keep |
| Pillow | 12.2.0 | 20,987,149 | REQUIRED image handling |
| networkx | 3.6.1 | 16,572,880 | REQUIRED by PaddlePaddle metadata; keep |
| cryptography | 50.0.2 | 15,618,167 | Transitive ModelScope dependency; keep |
| paddlex | 3.7.2 | 14,958,359 | REQUIRED by PaddleOCR 3.7 OCR-core extra |
| pip | 24.0 | 14,467,531 | DEV_ONLY/package management; removed in candidate after build validation |
| uvloop | 0.23.0 | 14,088,962 | Installed through `uvicorn[standard]`; keep absent an isolated test |
| pillow-avif-plugin | 1.6.0 | 12,832,068 | OPTIONAL_LAZY; supports the existing AVIF upload path |
| hf-xet | 1.6.0 | 12,509,370 | Transitive model-hub dependency; UNKNOWN for broader supported flows |
| shapely | 2.1.2 | 12,241,063 | PaddleX OCR-core dependency; keep |
| setuptools | 79.0.1 | 9,510,493 | REQUIRED by installed ModelScope metadata; not removed |
| pycryptodome | 3.23.0 | 9,252,928 | Transitive PaddleX/SDK dependency; UNKNOWN; keep |
| pypdfium2 | 5.13.0 | 8,689,556 | PaddleX OCR-core dependency; keep |
| aiohttp | 3.14.3 | 7,726,105 | PaddleOCR/PaddleX dependency; keep |

The v3.2 image has 86 installed Python distributions. The metadata-file-size
sum is 1,617,022,988 bytes, larger than the actual site-packages directory
because wheel distributions can overlap paths.

### Debian packages and native dependencies

The image contains 192 installed Debian packages (391,533 KiB total according
to `Installed-Size`). Largest entries observed:

| Package | Installed KiB | Role/decision |
|---|---:|---|
| libllvm15 | 114,610 | Pulled through the Mesa GL driver chain; not independently removed |
| libicu72 | 36,170 | Tesseract/native text stack dependency |
| libgl1-mesa-dri | 25,254 | Mesa GL driver dependency |
| libz3-4 | 22,767 | LLVM dependency |
| coreutils | 18,062 | Debian base/runtime tools |
| libc6 | 13,001 | Required system runtime |
| tesseract-ocr-osd | 10,331 | Installed through the Tesseract OCR package set |
| perl-base | 7,639 | Debian package/runtime dependency |
| bash | 7,164 | Debian base |
| dpkg | 6,409 | Debian base/package metadata |
| libssl3 | 6,041 | Native network/security dependency |
| util-linux | 4,978 | Debian base |
| apt | 4,232 | Debian package tooling |
| libglib2.0-0 | 4,135 | OpenCV/Tesseract native dependency |
| tesseract-ocr-eng | 4,032 | REQUIRED_FALLBACK language data |
| libtesseract5 | 3,514 | REQUIRED_FALLBACK engine |
| libgnutls30 | 3,396 | Transitive system dependency |
| libapt-pkg6.0 | 3,297 | Debian package tooling |
| tar | 3,144 | Debian base |
| fonts-dejavu-core | 2,960 | Tesseract/rendering dependency |

`libgl1` brings the `libglx0`/Mesa path; the active contrib OpenCV binary
links against `libGL.so.1`, `libGLX.so.0`, and `libglib-2.0.so.0`. Removing
those packages without changing the supported OpenCV runtime is not safe based
on current evidence. Tesseract and its English/French data are an active
fallback and were retained. The Dockerfile already uses
`--no-install-recommends` and removes `/var/lib/apt/lists/*` in the same layer.
Compiler/build packages are in the builder stage, not the runtime stage.

### Docker layers and artifacts

Largest `docker history` entries for v3.2:

| Layer instruction | Size | Purpose/cacheability |
|---|---:|---|
| `COPY /opt/venv /opt/venv` | 1.53 GB | Python dependencies; changes when the dependency environment changes |
| runtime `apt-get install ...` | 260 MB | Tesseract, GL/OpenCV, and native runtime dependencies; stable layer |
| Python base Debian layer | 74.8 MB | Base image |
| Python runtime installation layer | 45.9 MB | Official Python image |
| `COPY docker_assets/paddle /opt/ocr-models` | 12.8 MB | Required model weights; stable layer |
| `COPY app ./app` | 854 KB | Application source; code-only change layer |
| user/runtime-directory creation | 8.93 KB | Non-root runtime setup |
| copied requirements file | 303 B | In-image manifest |

The runtime stage has 11 root filesystem layers. No pip cache, apt lists,
wheel files, build directories, tests, `.git`, `.venv`, local PDFs, or private
ground truth were found in the v3.2 image. `/root` cache was empty. The build
context allowlist includes only requirements, app, model assets, and the
verified Paddle wheel; the initial isolated build transferred about 208.5 MB,
mostly the ~194.8 MB Paddle wheel used by the builder. Candidate and baseline
context inputs are otherwise the same, so the optimization does not reduce
context size.

## Dependency and import audit

Direct pinned deployment requirements before the experiment:

| Direct requirement | Production use | Classification |
|---|---|---|
| fastapi 0.137.2 | API routes, health, OpenAPI | REQUIRED |
| uvicorn[standard] 0.49.0 | Single-worker server | REQUIRED |
| python-multipart 0.0.32 | Multipart PDF uploads | REQUIRED |
| pydantic 2.13.4 | API/application schemas | REQUIRED |
| pydantic-settings 2.14.2 | Environment configuration | REQUIRED |
| python-dateutil 2.9.0.post0 | Date parsing | REQUIRED |
| PyMuPDF 1.27.2.3 | PDF rasterization/page loading | REQUIRED |
| opencv-python-headless 4.13.0.92 | Explicit direct dependency, but masked by PaddleX's contrib package in this export | REDUNDANT here |
| numpy 2.3.5 | Image arrays/preprocessing | REQUIRED |
| paddleocr 3.7.0 | OCR orchestration | REQUIRED |
| paddlepaddle 3.3.1 | CPU OCR inference | REQUIRED |
| pytesseract 0.3.13 | Tesseract fallback bridge | REQUIRED_FALLBACK |
| Pillow 12.2.0 | Raster/image input handling | REQUIRED |
| pillow-avif-plugin 1.6.0 | AVIF-only decoder path | OPTIONAL_LAZY |

PaddleOCR 3.7 installs `paddlex[ocr-core]`; its dependency metadata explicitly
requires `opencv-contrib-python==4.10.0.84`, `pandas`, `modelscope`,
`pypdfium2`, `shapely`, and `pyclipper`. The installed `cv2.__version__` in
v3.2 is **4.10.0**, from the contrib distribution, even though the direct
headless distribution is also installed. The application itself uses common
OpenCV APIs in `file_loader.py`, `preprocessing.py`, `table_regions.py`, and
`ocr_engine.py`; the active imported implementation remains the same in the
candidate.

Static and exercised dynamic import paths:

- PDF/image path: PyMuPDF, Pillow, OpenCV, and NumPy.
- Paddle path: `app.services.ocr_engine._get_paddle_ocr` lazily imports
  `paddleocr.PaddleOCR`; the configured detector and recognizer are local
  `/opt/ocr-models` assets.
- Fallback: `pytesseract` invokes system Tesseract with `eng` and `fra` data.
- HTTP path: FastAPI, Pydantic, Starlette, Uvicorn and multipart handling.
- AVIF decoder import is lazy and format-specific.
- No pytest, coverage, notebook, formatter, profiler, Torch, SciPy, scikit-image,
  or visualization packages were found in the production image.

Untraced transitive packages remain **UNKNOWN** unless required by upstream
PaddleX/PaddleOCR metadata. Lack of import during a single request is not proof
that they are removable. Optional PaddleX extras for layout, video, GenAI,
translation, and other non-OCR pipelines are not installed in this export.

## Experiments

### Accepted in the isolated experiment; not yet released

1. Removed the explicit `opencv-python-headless==4.13.0.92` line from a copied,
   isolated deployment requirements file. PaddleX's required
   `opencv-contrib-python==4.10.0.84` remained. This avoids the duplicate
   distribution/overlapping `cv2` namespace. The active OpenCV version remained
   4.10.0, and all six public dossier outputs matched exactly.
2. Ran `pip check` successfully in the builder, then removed the runtime `pip`
   distribution. Production code does not install packages. `setuptools` was
   deliberately retained because installed ModelScope metadata declares it.

The experimental image is local-only:

- Tag: `ocr-ruspina:3.3-exp-opencv-tools`
- Image ID: `sha256:939b0d0774377db2081242d6ece53e4e6ea01cec4198008b594aa1641b9196f6`
- Size: 1,834,186,378 bytes, 11 layers, Linux/amd64, `appuser`, one worker.
- v3.2 minus candidate: **98,733,256 bytes (5.11%)**.
- Actual site-packages directory: 1,544,690,289 → 1,445,449,838 bytes.
- Actual `/usr`, model, and app sizes were unchanged within measurement
  (models remain 12,769,241 bytes; `/usr` 381,271,323 bytes).
- Candidate has 84 Python distributions; v3.2 had 86. `pip` is absent and
  `setuptools` remains. The successful `pip check` ran before the removal of
  `pip`; a package manager is intentionally not present in the final candidate.

The combined reduction is just over the approximate 5% materiality threshold.
The OpenCV cleanup also removes a genuine dual-distribution namespace/version
conflict. The experiment does **not** justify changing the OCR model, Paddle,
OpenCV contrib, Tesseract, language data, API, extraction, routing, or worker
count.

### Rejected or deferred

- An initial candidate edit also removed `setuptools`; this was stopped before
  a completed build after metadata showed ModelScope declares setuptools. No
  such removal is in the successful experiment.
- Removing OpenCV contrib or its Mesa/GL dependency chain is not supported by
  the current PaddleX OCR-core dependency metadata or native linkage evidence.
- Tesseract and `eng`/`fra` language data are active fallback behavior and
  cannot be removed.
- No other transitive dependency was removed based solely on static or dynamic
  import absence.
- No architecture/model/Python-version change, optional-feature refactor, or
  system-package override was attempted.

## Correctness, API, offline, and privacy results

- Source suite at the starting v3.2 commit: **687 passed, 0 failures, 1
  existing Starlette/httpx deprecation warning**.
- Deployment export suite: all **33 test progress markers** appeared, but
  `pytest` did not print its summary or exit, including when run by test file
  and with plugin autoload disabled. Sessions were interrupted. This is
  **incomplete verification**, not recorded as 33 passed. It blocks release.
- Builder `pip check`: **No broken requirements found** before removing pip.
- Health: `/api/v1/health` returned `ok` on both images.
- OpenAPI exposes the same three paths on both images:
  `/api/v1/health`, `/api/v1/dossiers/process`,
  `/api/v1/dossiers/resolve-routing`.
- Synthetic offline Paddle smoke (`--network none`, 4 CPU, 8 GiB): both models
  loaded from packaged paths, two requests reused one model initialization,
  and both images recognized the synthetic invoice. English and French
  Tesseract checks passed; Tesseract reported 5.3.0. Peak process RSS for this
  isolated script: 798.5 MiB v3.2 / 792.6 MiB candidate.
- Real API Page-1–3 processing of the private regression dossier succeeded on
  both images; public response `data` was exactly equal. A second fresh cold
  request also produced identical data.
- All six local canonical dossiers were uploaded through the live
  `/api/v1/dossiers/process` endpoint against both images. Public `data` was
  exactly equal for every dossier, including status/review results, page
  groups, and line-item counts. No values are copied here.
- Fresh candidate startup/health, PDF processing, packaged-model reload,
  non-root identity, one worker, and network-isolated synthetic OCR were
  exercised. The deployment image audit reported zero forbidden files,
  source paths, or secret environment keys.
- Client PDFs and response JSON were mounted/uploaded only from local paths;
  response artifacts remain ignored under `local_data/`. The build context and
  candidate image contain no client PDF, OCR response, ground truth, `.git`,
  `.venv`, secret, or machine-specific source path.

## Performance and resource comparison

All live API requests used the same host and 4 CPU / 8 GiB container limit.
Cold values are two measurements per image; warm values are three requests on
the same already-initialized container.

| Measure | v3.2 | Candidate | Interpretation |
|---|---:|---:|---|
| Image bytes | 1,932,919,634 | 1,834,186,378 | Candidate saves 98,733,256 bytes / 5.11% |
| Layers | 11 | 11 | No layer-count increase |
| Idle RSS before model load | 77.14 MiB | 78.72 MiB | ~2% candidate increase, not material |
| Cold full-dossier request 1 | 98.52 s | 107.75 s | Single-run variance/outlier |
| Cold full-dossier request 2 | 96.88 s | 94.47 s | Fresh container, no prior writable cache |
| Two-run cold mean | 97.70 s | 101.11 s | Candidate +3.5%; under 5% gate, sample small |
| Warm full-dossier request samples | 14.09, 13.84, 15.14 s | 12.29, 12.78, 12.24 s | Candidate median 12.29 s, ~12.8% faster |
| Process high-water RSS after real request | 1,575,236 KiB | 1,508,832 KiB | Candidate ~4.2% lower |
| Cgroup memory peak | 1,399,640,064 B | 1,304,891,392 B | Candidate ~6.8% lower |

The candidate shows no measured warm-latency or memory regression. Cold samples
are limited to two and include a first-run outlier; they do not establish a
material cold regression. Synthetic cold/warm tests show model initialization
once and subsequent model reuse.

The isolated dependency install took about 8.8 minutes due to slow package-index
downloads; the package layer is cacheable after that install. A code-only
rebuild timer was not separately measured. The experiment's image-layer digest
comparison showed the venv layer changed; because the test context was copied,
model/app copy-layer digests also differed. A future registry pull is expected
to transfer the changed ~1.43 GB venv layer plus any changed upper layers; no
registry transfer was performed or represented as an exact network measurement.
No TAR compression experiment was performed because no release TAR was made.

## Release decision and next steps

The measured candidate clears the approximate material-size and measured
behavior/performance gates, but the deployment export test suite did not
terminate cleanly. **Do not merge, push, tag, create a v3.3 release TAR, or
replace v3.2 yet.** No remote registry was pushed to. The original v3.2 tag,
image, and TAR remain the rollback point.

Next work, after the export test-runner shutdown issue is resolved:

1. Rerun all 33 export tests and require a normal pytest summary and exit code.
2. Re-run source and export suites against the final tracked/export
   configuration.
3. If tests remain clean, synchronize only the two measured packaging changes
   (remove redundant headless requirement from this pinned deployment export;
   remove pip from runtime after builder `pip check`) and rebuild from the
   actual clean export, not the copied experiment context.
4. Recheck exact OpenAPI/response parity, the six-dossier set, offline OCR,
   Tesseract, package integrity, image size, resource measurements, and
   privacy. Preserve v3.2 throughout.
5. Only then consider committing/pushing the feature branch, merging to main,
   publishing a v3.3 TAR, and tagging `v3.3`.

Current Git result: no product code, requirements, export files, or release
files were changed; no commit, push, merge, or tag was created. The audit file
and experimental artifacts are local pending the test-runner gate.
