"""Check a curated dataset the way the homelab worker will read it (P6, 2026-11-16).

Run with the worker's own environment, so the token counts are the trainer's:

    PYTHONPATH=~/harnesswalk-lab/bin ~/harnesswalk-lab/.venv/bin/python verify_dataset.py \\
        ~/harnesswalk-lab/cache/Qwen3.5-4B <dataset dir with train.jsonl and val.jsonl> [6144]

Prints, as JSON: examples per split, pack, kind, source and teacher; samples (one per
assistant message, as the worker trains) per split; the token length distribution of the
samples (prompt as Ollama renders it plus the reply); and how many examples the cap drops.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from pipeline import encode  # the worker's renderer and sample builder


def main() -> int:
    from transformers import AutoTokenizer

    tok_path, folder = sys.argv[1], Path(sys.argv[2])
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 6144
    tok = AutoTokenizer.from_pretrained(tok_path)
    out: dict = {"folder": str(folder), "cap": cap}
    for split in ("train", "val"):
        rows = [json.loads(x) for x in (folder / f"{split}.jsonl").read_text().splitlines() if x]
        lengths, samples, dropped = [], 0, 0
        for ex in rows:
            enc = encode(tok, ex, 10**9)
            if any(len(s["input_ids"]) > cap for s in enc or []):
                dropped += 1
                continue
            samples += len(enc or [])
            lengths += [len(s["input_ids"]) for s in enc or []]
        lengths.sort()

        def pct(p: float, xs: list[int] = lengths) -> int | None:
            return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None

        meta = [r.get("meta") or {} for r in rows]
        out[split] = {
            "examples": len(rows),
            "samples": samples,
            "dropped_over_cap": dropped,
            "tokens": {
                "p50": pct(0.5),
                "p90": pct(0.9),
                "p99": pct(0.99),
                "max": lengths[-1] if lengths else None,
                "mean": round(sum(lengths) / len(lengths)) if lengths else None,
                "over_4096": sum(1 for x in lengths if x > 4096),
            },  # fmt: skip
            "by_pack": dict(Counter(m.get("pack") for m in meta)),
            "by_kind": dict(Counter(m.get("kind") for m in meta)),
            "by_source": dict(Counter(m.get("source") for m in meta)),
            "by_teacher": dict(Counter(m.get("teacher") for m in meta)),
        }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
