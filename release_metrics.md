# Deterministic v1.0 Release Metrics

## Final Benchmark

| Area | Metric | Result |
| --- | --- | ---: |
| Metadata | Invoice number normalized accuracy | 100% |
| Metadata | Invoice date normalized accuracy | 100% |
| Financial | Amount TTC normalized accuracy | 76.92% |
| Party | Supplier canonical accuracy | 70% |
| Party | Customer canonical accuracy | 100% |
| Tables | Canonical line-item presence | 60% |
| Tables | Canonical exact row count | 52% |
| Tables | Canonical row count within ±1 | 68% |

## Benchmark Configuration

- Run ID: `v1_deterministic_50doc_01`
- Documents: 50
- Seed: 42
- OCR profile: `optimized_mobile_v4`
- Table profile: `p3_stable`
- OCR mode: `balanced`
- Workers: 1
- Document timeout: 120 seconds
- Configuration hash: `9699262229f6b19dc0ab0f5ad813adc79aa9a777c693ccb183ff46e6a4640f32`

## Architecture Summary

The v1 deterministic engine uses OCR, layout analysis, semantic blocks, candidate scoring, party ranking, financial reasoning, table reconstruction, validation, confidence calibration, and ERP readiness guardrails. The API remains compatible with earlier ERP JSON/export payloads while exposing richer debug information for human review and benchmarking.

## Test Summary

Final validation commands:

```powershell
python -m compileall app scripts tests
python -m pytest -q
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --run-id optimized_p2_7_party_50doc_01 --size small --seed 42 --ocr-profile optimized_mobile_v4 --workers 1 --document-timeout 120 --reuse-ocr --restart
```

Latest full test result:

- `260 passed`
- `1 warning` from Starlette/FastAPI `httpx` deprecation

Latest focused release test result:

- `67 passed`

Latest compile check:

- passed

## Known Limitations

- Table stretch targets were not reached safely under `p3_stable`; stability was preferred over a risky final heuristic.
- Some external datasets contain weak or explicit-zero table labels, so canonical table metrics should be read together with adapter diagnostics.
- ERP readiness is intentionally conservative.
- Arabic support is partial.
- The Hybrid LLM phase should target unresolved tables, fragmented OCR, and ambiguous party/financial cases.

## Recommended Future Work

- Hybrid LLM recovery for unresolved rows and multi-line item descriptions.
- LLM-assisted candidate re-ranking using deterministic Top-N evidence.
- Human correction memory expansion.
- Larger overnight benchmark after v1 tag.

## Readiness

The deterministic engine is ready to serve as the frozen baseline for Hybrid LLM development.
