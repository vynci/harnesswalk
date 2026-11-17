"""The size-curve table (Markdown) from `tune testrun --report` JSON files, one per model.

python3 curve_table.py <dir with <model>.json files>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def cell(v: dict) -> str:
    lo, hi = v.get("ci95") or [0, 0]
    rate = 100 * v.get("rate", 0)
    return f"{v.get('passed')}/{v.get('n')} ({rate:.1f}%, {100 * lo:.1f}-{100 * hi:.1f})"


def main() -> int:
    rows = []
    for f in sorted(Path(sys.argv[1]).glob("*.json")):
        d = json.loads(f.read_text())
        p, w = d["by_pack"], d.get("tool_split_by_pack") or {}
        wtn = {k: (w.get(k) or {}).get("write tool not called", 0) for k in sorted(w)}
        rows.append(
            (
                d["all"]["rate"],
                f"| {f.stem} | {cell(d['all'])} | {cell(p.get('ecommerce', {}))} | "
                f"{cell(p.get('logistics', {}))} | {cell(p.get('device_fleet', {}))} | "
                f"{sum(wtn.values())} ({', '.join(f'{k} {v}' for k, v in wtn.items())}) | "
                f"{d.get('mean_s')} / {d.get('p95_s')} | {d.get('decode_tok_s')} | "
                f"{d.get('usd')} |",
            )
        )
    print(
        "| model | all (95% CI) | ecommerce | logistics | device_fleet (held out) |"
        " write tool not called | mean / p95 s per case | decode tok/s | USD |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for _, r in sorted(rows):
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
