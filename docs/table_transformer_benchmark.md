# Table Transformer Benchmark

Run:

```powershell
.venv\Scripts\python.exe scripts\benchmark_table_transformer.py --limit 10
```

Outputs are written to:

`dataset/reports/table_transformer/<run-id>/`

Generated files:

- `table_transformer_metrics.json`
- `table_transformer_report.md`
- `comparison_with_p3.csv`
- `document_results.csv`
- `latency.csv`

Recommendation rule: Table Transformer remains optional until verified labels
demonstrate consistent row-level improvement over P3 Stable.
