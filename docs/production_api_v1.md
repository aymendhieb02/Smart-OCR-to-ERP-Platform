# Production API v1

This contract describes the **verified v3.3 production image**, not every route in the source-development app. OpenAPI and interactive forms are served at `/docs` by a running image.

## Public routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Health check; does not initialize OCR. |
| `POST` | `/api/v1/dossiers/process` | Submit one PDF/image as multipart field `file`; returns a completed dossier or routing choices for human review. |
| `POST` | `/api/v1/dossiers/resolve-routing` | Resolve all ambiguous physical-page assignments using the returned in-memory routing session. |

The production OpenAPI schema intentionally exposes only these routes. Legacy backend routes are retained for compatibility but hidden from production OpenAPI; they are not the supported v1 integration surface.

### Source-checkout distinction

The checked-in `app/main.py` currently mounts the legacy source router directly. Running `python run.py` from this repository is therefore a source-development app and does not reproduce the production image’s versioned public facade. The v3.3 deployment export adds the v1 facade and hides legacy paths from OpenAPI. This release/source packaging difference must be resolved before treating a source-built image as equivalent to the v3.3 production artifact.

## Processing flow

Send one PDF or image as multipart field `file`:

```bash
curl -F "file=@synthetic.pdf" http://localhost:8000/api/v1/dossiers/process
```

Supported extensions are `.pdf`, `.png`, `.jpg`, `.jpeg`, `.jfif`, `.tif`, `.tiff`, `.bmp`, and `.avif`; the configured upload limit is 25 MiB. Extension support does not guarantee that corrupt or unreadable file content will be accepted.

A completed response has `api_version: "1.0"`, `status: "completed"`, and `data` keys for `page1`, `page2`, and `page3`. These represent the producer invoice, RUSPINA invoice, and customs declaration semantic groups. Absent groups are `null`; missing fields in present groups are `null`. Monetary values are serialized as strings, and a line item contains `description`, `quantity`, `unit`, `unit_price`, and `line_total`.

The [synthetic response example](examples/production-response.synthetic.json) is deliberately abbreviated to show the envelope and common fields; it is not the complete page schema. All values are invented.

## Human routing

If the initial response has `status: "review_required"`, it supplies the physical pages requiring an assignment and a short-lived `routing_session_id`. Resolve the listed pages by posting each selected `semantic_group` (`page1`, `page2`, or `page3`) to `/api/v1/dossiers/resolve-routing`. Successful resolution returns the same completed envelope.

Routing sessions are held in process memory, expire after about 30 minutes, and are bounded to 16 concurrent sessions. Keep both calls on the same single-worker service instance. If a session expires or is lost after restart, upload the document again. Routing resolution reuses retained OCR evidence rather than rerunning full-page OCR.

`completed` means processing finished; it does not mean every extracted value is correct or ERP validation passed. The public v1 JSON contains effective business values, not OCR text, evidence boxes, confidence detail, or review objects.

## Deployment and security

The API has no built-in authentication. Restrict it to an access-controlled network or place it behind an authenticated gateway. Do not expose it directly to untrusted networks. The v3.3 container listens on port `8000`; its Docker health check calls `/api/v1/health`.
