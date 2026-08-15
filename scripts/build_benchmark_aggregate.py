"""Build the competitor benchmark aggregate without shipping comparator text.

The input snapshots are privacy-minimised exports produced by
`import_reviews_snapshot.py`. They may live in the private build workspace. The
output contains counts, rates and hashed evidence references only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.connectors.benchmark import build_competitor_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wolf", type=Path, required=True)
    parser.add_argument("--browns-mill", type=Path, required=True)
    parser.add_argument("--alfred-tup-holmes", type=Path, required=True)
    parser.add_argument("--chastain-park", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort = [
        ("wolf-creek-atlanta", "Wolf Creek Golf Club", args.wolf),
        ("browns-mill-atlanta", "Brown's Mill Golf Course", args.browns_mill),
        ("alfred-tup-holmes-atlanta", "Alfred Tup Holmes Golf Course", args.alfred_tup_holmes),
        ("chastain-park-atlanta", "Chastain Park Golf Course", args.chastain_park),
    ]
    aggregate = build_competitor_benchmark(cohort)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"courses": len(aggregate["cohort"]),
                      "comparisons": len(aggregate["comparisons"]),
                      "recommendations": len(aggregate["recommendations"])}))


if __name__ == "__main__":
    main()
