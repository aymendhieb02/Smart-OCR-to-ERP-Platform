# Documentation

Start here for the product and its boundaries:

- [Architecture overview](architecture_overview.md) — request flow, modules, API groups, and trust boundaries.
- [Editable production pipeline diagram](architecture/README_PIPELINE.mmd) — Mermaid source used by the repository README.
- [Production API v1](production_api_v1.md) — the versioned API contract, routing review, and source/deployment distinction.
- [Synthetic production response](examples/production-response.synthetic.json) — abbreviated fake-valued JSON example.
- [README/repository audit](README_AUDIT.md) — source-of-truth checks and presentation decisions.
- [Windows setup](setup_windows.md) — local installation, running the app, and keeping private data local.
- [Limitations](limitations.md) — capabilities and production features that are not included.
- [Benchmark summary](benchmark_summary.md) — benchmark types and how to interpret results.
- [Tiered evaluation](benchmarks/tiered-evaluation.md) — fast, medium, cached, and full dataset evaluation.
- [Multi-dataset benchmark](benchmarks/multi-dataset.md) — sampling external datasets and generating reports.
- [UI localization](ui_localization.md) — locale behavior and translation conventions.

Generated reports, client documents, OCR evidence, and private ground truth are runtime/user data, not public documentation. Keep those artifacts out of commits.
