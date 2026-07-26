# Layout Model Benchmark

This benchmark compares the current heuristic layout analyzer with the optional DocLayout-YOLO visual layout model.

Headline metric: unmapped OCR text ratio, where lower is better.

## Summary

- Documents attempted: 3
- Successful documents: 3
- Heuristic average unmapped ratio: 0.3542
- Layout model average unmapped ratio: None
- Layout model available: False

## Recommendation Gate

Keep optional: no model detections were available in this environment.

The pretrained model remains optional and disabled by default. Do not enable `INVOICE_OCR_ENABLE_LAYOUT_MODEL` in production until repeated benchmarks show a clear unmapped-text-ratio improvement without regressions.

## Notes

DocLayout-YOLO uses `juliozhao/DocLayout-YOLO-DocStructBench` with `doclayout_yolo`/`YOLOv10`. If dependencies or local weights are missing, the application falls back to the heuristic analyzer.
