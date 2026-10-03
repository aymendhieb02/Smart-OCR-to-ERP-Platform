# README and repository presentation audit

Audit basis: the checked-in source at the `v3.3` tag, tracked source/tests/docs, the v3.3 deployment release manifest and API contract available to the maintainer, and the public GitHub repository page. This file records findings before the README rewrite; it does not change runtime behavior.

## Current README

### Accurate

- The product is an OCR-assisted extraction and review workflow, not simply text recognition.
- Extraction is distinct from validation and ERP readiness; uncertain or inconsistent data can require review.
- The review UI handles multi-page dossiers and keeps physical-page preview/navigation distinct from logical-document fields.
- Client documents and human-verified labels must stay local; Arabic UI/RTL is not implemented.
- The root `Dockerfile` is not the separately built v3.3 production image. Keeping this distinction is important.

### Outdated or misleading

- Its API section lists development/legacy paths such as `/process-dossier` and `/process-invoice` as the main API. The verified production image publishes only `GET /api/v1/health`, `POST /api/v1/dossiers/process`, and `POST /api/v1/dossiers/resolve-routing`; the remaining backend routes are hidden from the production OpenAPI schema.
- The checked-in source app and the production export are not identical API surfaces: `app/main.py` mounts the legacy router directly, while the release export adds a versioned public facade and hides the legacy router from OpenAPI. README instructions must label source-development behavior versus production behavior.
- The source `Dockerfile` is a general build: it does not copy the release model assets, run as the audited non-root user, or define the production health check / one-worker command. It must not be presented as the v3.3 deployment image.
- Local source requirements are broad ranges. The production export uses a separately pinned runtime manifest. The README must not imply that installing the source requirements reproduces the immutable v3.3 image.
- Earlier README/setup copy suggested models may download on first OCR use. That can apply to a fresh source development environment, but not to the verified production image: it contains the two PP-OCRv4 mobile model directories and passed an offline OCR smoke test.

### Missing

- v3.3 tag/release context and the verified production image facts.
- Exact versioned production API, routing-review behavior, and no-auth limitation.
- Test gates: source suite and deployment-export suite, plus the categories exercised by the release checks.
- A complete, explicitly synthetic production-response example.
- The difference between production-tested invoice/trade dossier flow and broader experimental code in the repository.
- Offline inference, model packaging, privacy boundary, honest performance context, and absence of a live ERP connector.
- License status and the lack of an explicit public registry/release-download URL in the repository.

### Content to shorten or relocate

- Keep the root README focused on product purpose, current production boundary, quick start, API, verified release, safety, and links.
- Put the full source/developer module map in `docs/architecture_overview.md` and the production contract in `docs/production_api_v1.md`.
- Keep benchmark commands/methodology in `docs/benchmarks/`; link concise measurements from the root rather than repeating instructions.
- Keep internal handoff/audit history out of the first-screen navigation. The tracked runtime-footprint audit contains machine-specific operational details and is not linked from the public-facing README.

## Repository structure and root cleanup candidates

| Path | Role / presentation note |
| --- | --- |
| `app/` | Checked-in FastAPI source, schemas, extraction services, review UI, and legacy routes. |
| `tests/` | Source regression, API/service, frontend contract, and benchmark tests. |
| `docs/` | Product/developer documentation, release audits, and experiments; use a short curated index. |
| `scripts/` | Evaluation, benchmark, migration, and diagnostic tools. |
| `dataset/` | Synthetic/public benchmark assets and schemas; generated report paths are ignored. |
| `Dockerfile` | General source build, not the v3.3 production export Dockerfile. |
| `requirements*.txt` | Broad source/dev/optional-ML dependencies; production pins are in the deployment export. |
| `CHANGELOG_v1.md`, `release_metrics.md` | Historical v1 material; retain, but do not present as current v3 release notes. |
| `benchmark_snapshot.json` | Root-level generated-looking snapshot; confirm ownership/references before relocating or deleting. |
| `app.rar`, `invoice-ocr-erp.rar` | Opaque root archives; inspect provenance, contents, Git history, and references before any cleanup. Not needed in the README tree. |
| `README_BENCHMARK.md`, `README_MULTI_DATASET_BENCHMARK.md` | Already moved to `docs/benchmarks/` in the preceding presentation cleanup; links should use their new paths. |

No files are moved or deleted by this audit. In particular, private local ground truth and local audit artifacts are not part of the documentation change.

## Claim verification checklist

| Claim intended for README | Evidence | Result |
| --- | --- | --- |
| Current source release is tagged v3.3 | Git tag `v3.3` points to the v3.3 runtime-footprint merge; deployment `RELEASE_MANIFEST.txt` names release v3.3 | Verified |
| Production image metadata | v3.3 deployment manifest and handoff: `ocr-ruspina:3.3`, Linux/amd64, 1,834,186,378 bytes, 11 layers, `appuser` UID 10001, one worker, port 8000 | Verified in release artifacts; Docker daemon was unavailable for a fresh local `docker image inspect` during this audit |
| Packaged/offline OCR | Export Dockerfile copies PP-OCRv4 detector/recognizer assets; manifest records network-isolated OCR/model-reuse pass | Verified in release artifacts |
| OCR engines/models | Pinned export requirements and release notes: PaddleOCR 3.7.0, PaddlePaddle 3.3.1 CPU, PP-OCRv4 mobile detector + English recognizer; Tesseract 5.3.0 with `eng`/`fra` | Verified in release artifacts |
| Production API | Export `API_CONTRACT.md`, public routes and OpenAPI test evidence: exactly three public v1 routes; legacy routes hidden from production schema | Verified |
| Source API distinction | Checked-in `app/main.py` / `app/api/routes.py` mount the non-versioned legacy router; v1 facade exists in the export, not this source tree | Verified; disclose explicitly |
| Test totals | v3.3 `RELEASE_MANIFEST.txt`: 687 source tests and 33 deployment-export tests, each with one known deprecation warning; current source suite will be rerun for this documentation branch | Verified in release artifacts |
| Six-dossier compatibility | v3.3 release notes/manifest: public response data matched v3.2 across six canonical local dossiers | Verified; publish no dossier values |
| Image-footprint delta | Release audit: 98,733,256 bytes / 5.11% smaller than v3.2 | Verified; image size only, not a general speed claim |
| Warm/cold timing | Runtime audit: same 4-vCPU/8-GiB host; three warm requests and two cold requests per image; candidate warm median 12.29 s vs v3.2 14.09 s, cold sample is small | Verified measurement with explicit sample/host caveat |
| No model downloads at production inference | Network-isolated synthetic Paddle smoke passed with packaged models; source-development installs are a different path | Verified only for the production image |
| No public image download URL | Release TAR is in the local release bundle; no public registry/image URL is recorded in the release manifest or GitHub Packages/Release page | No public distribution URL verified; say so plainly |
| License | No root `LICENSE` file is present | Verified; do not add a license badge or imply open-source license terms |
| GitHub About metadata | Public repository page displays no description/website; existing topic list is long but mostly on-topic | Verified; settings are not changed by this documentation-only branch |

## Presentation decisions

- Use only technology/release badges that are descriptive and link to real repository state. Do not add a CI badge: no CI workflow is present. Do not add a license badge or claim a license.
- Link only maintained, relevant guides from the README; keep operational handoffs and experimental reports secondary.
- Separate v3.3 production image/API claims from the checked-in source-development app and root Dockerfile.
- Do not claim general support for receipts, purchase orders, or arbitrary business forms based only on broad file-format acceptance. Describe the verified producer/RUSPINA/customs dossier focus and the six-dossier regression evidence.
- Do not claim a measured OCR accuracy percentage. Confidence is not accuracy; the verified release report establishes parity and runtime checks, not broad field-level accuracy.
- Do not claim legal/privacy certification. State the technical offline/no-cloud-LLM and data-packaging boundaries only.
