"""Export one model's test-set results, case by case, from `tune testrun` state directories.

    python3 export_results.py OUT.json FROZEN_SCENARIOS.jsonl STATE [STATE ...]

A later state's result for a case replaces an earlier one's (the re-run of the 29 approved SMS
clears whose expectation the freeze fixed). Only the frozen test set's cases are kept. The
reply text is left out; each case keeps its pack, kind, status, failing scorers, the
`tool_calls` failure split, the tools it called, timing and cost. Standard library only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    out, frozen, states = Path(argv[0]), Path(argv[1]), [Path(p) for p in argv[2:]]
    keep = {}
    for line in frozen.read_text(encoding="utf-8").splitlines():
        if line.strip():
            s = json.loads(line)
            keep[f"{s['pack']}/{s['case']['id']}"] = s["kind"]
    cases: dict[str, dict] = {}
    usd = 0.0
    for state in states:
        for f in sorted((state / "shards").glob("*.done.json")):
            rec = json.loads(f.read_text(encoding="utf-8"))
            usd += float(rec.get("usd") or 0.0)
            for c in rec["cases"]:
                key = f"{rec['pack']}/{c['case_id']}"
                if key not in keep:
                    continue
                cases[key] = {
                    "pack": rec["pack"],
                    "case_id": c["case_id"],
                    "kind": keep[key],
                    "status": c["status"],
                    "failed": c.get("failed") or [],
                    "tool_split": c.get("tool_split"),
                    "tool_calls": c.get("tool_calls") or [],
                    "timing": c.get("timing"),
                    "usd": c.get("usd"),
                }
    missing = sorted(set(keep) - set(cases))
    data = {
        "states": [str(s) for s in states],
        "cases": [cases[k] for k in sorted(cases)],
        "missing": missing,
        "usd_all_states": round(usd, 4),
    }
    lines = ",\n".join(json.dumps(c, separators=(",", ":")) for c in data["cases"])
    head = {k: v for k, v in data.items() if k != "cases"}
    text = json.dumps(head)[:-1] + ', "cases": [\n' + lines + "\n]}\n"
    json.loads(text)  # one case per line, still one JSON document
    out.write_text(text, encoding="utf-8")
    print(f"{out.name}: {len(cases)} cases, {len(missing)} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
