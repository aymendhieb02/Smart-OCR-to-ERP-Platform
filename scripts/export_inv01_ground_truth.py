from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.pipeline_runner import process_dossier_file
from scripts.dossier_ground_truth import (
    build_review_template,
    current_commit,
    serialize_machine_output,
    write_json,
)

def main() -> int:
    parser = argparse.ArgumentParser(description="Export a frozen dossier prediction and human-review template.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "local_data/dossier_ground_truth/inv01")
    args = parser.parse_args()

    result = process_dossier_file(args.source, original_filename=args.source.name)
    machine = serialize_machine_output(
        result,
        source_path=args.source,
        pipeline_commit=current_commit(ROOT),
    )
    template = build_review_template(machine)
    write_json(args.output_dir / "machine_output.json", machine)
    write_json(args.output_dir / "ground_truth_review_template.json", template)
    print(f"Wrote {args.output_dir / 'machine_output.json'}")
    print(f"Wrote {args.output_dir / 'ground_truth_review_template.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
