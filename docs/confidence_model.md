# Confidence Model

The project exposes a **Composite Confidence Index** for review prioritization.

It is **not a calibrated probability** and must not be presented as true extraction accuracy.

## Components

The index combines bounded scores from:

- OCR confidence
- layout confidence
- table reconstruction confidence
- field selection score
- financial consistency score
- row validation score
- ERP readiness score

Each component is normalized to the range `0..1`. Invalid values and missing values are handled defensively.

## Meaning

The score answers: "How confidently should this document move through the review workflow?"

It does not answer: "What is the probability that every field is correct?"

## Current Weights

The weights are defined in `app/services/confidence_engine.py`.

They are engineering weights, not learned calibration parameters.

## Limitations

- A high index can still hide a wrong field.
- A low index can still contain correct values.
- OCR confidence is not ground-truth accuracy.
- Manual verification is required before making accuracy claims.

## Future Calibration

After manually verified labels are available, run threshold calibration on prediction/ground-truth pairs and report precision, recall, and F1 per field.

Production code should not auto-change thresholds from calibration output without human review.
