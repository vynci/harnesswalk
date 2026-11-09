"""Run the pack's golden set through this harness on the fake provider and print each case.

    python -m evals.run_golden          # or: make eval

Every case is one or more turns with the case's fake fixture, scored by the deterministic
scorers in harness/core/evals.py. Exit 1 when a case fails, so CI gates on it.
"""

from __future__ import annotations

import sys
import tempfile
import time
from typing import Any

from harness.core import config, evals
from harness.core.runtime import Harness


def run(data: str | None = None, quiet: bool = False) -> dict[str, Any]:
    h = Harness(config.load({"data": data or tempfile.mkdtemp(prefix="golden-"), "provider": "fake"}, env={}))
    run_id = evals.start(h, "dev", h.settings.default_pack)
    while h.db.one("eval_runs", run_id=run_id)["status"] == "running":
        time.sleep(0.1)
    result = h.db.one("eval_runs", run_id=run_id)
    for c in result["cases"]:
        failed = [f"{k}: {v['detail']}" for k, v in c["scores"].items() if not v["pass"]]
        if not quiet:
            print(f"{c['status'].upper():8} {c['case_id']}" + (f"  ({failed[0]})" if failed else ""))
    return result["totals"]


if __name__ == "__main__":
    totals = run()
    print(f"{totals['passed']}/{totals['cases']} golden cases passed, {totals['skipped']} skipped")
    sys.exit(0 if totals["failed"] == 0 else 1)
