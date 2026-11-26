"""Print measured LudoFabric costs/timings; does not call AI or start Unity."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameagent.metrics import summarize

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    metrics = project.parent / f"{project.name}.agent" / "metrics.jsonl"
    print(json.dumps(summarize(metrics), indent=2))
