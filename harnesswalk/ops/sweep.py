"""The TTL sweeper (P6): hard-delete every row of an expired anonymous public tenant.

Not a node. The server runs it every `public.sweep_interval_s` in public mode (0 turns it off),
and `harnesswalk sweep` runs it once from the command line (spec/contracts/gateway.md, "P6
additions").

Whose rows: the tenants `control/public.py` registered whose expiry has passed, plus any
`anon-` tenant whose first session is older than `public.session_ttl_s` (a tenant from before
the registry, or one whose registry row was lost), never one with a turn running in this
process. A tenant's eval runs bring their scratch tenants (`evalrun-<run_id>`) with them.

What goes: every row with that `tenant_id` in every store file (trace events, details and
turn index, ops events, sessions, turns, summaries and checkpoints, memory proposals, facts,
fact versions and episodes, approvals, eval runs and their cases, scores, feedback and
diagnoses, the per-tenant budget ledger), the tenant's vectors, its whole audit chain (the one
deletion the append-only trigger lets through, `AuditLog.purge_tenant`), its sandbox work
directories, and what this process keeps in memory for it. Dataset artifacts and releases are
per pack, not per tenant, and public mode cannot edit them, so they stay. The clock is
injectable for tests.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import time
from collections.abc import Callable
from typing import Any

from harnesswalk.control.public import TENANT_ID
from harnesswalk.events import iso
from harnesswalk.execution.sandbox import SAFE_NAME
from harnesswalk.ops.eval.offline import scratch_tenant

SCRATCH = re.compile(r"^evalrun-[A-Za-z0-9_.:-]{1,100}$")


class Sweeper:
    def __init__(self, harness: Any, clock: Callable[[], float] = time.time) -> None:
        self.h = harness
        self.clock = clock

    @property
    def ttl_s(self) -> float:
        return float(self.h.settings.get("public.session_ttl_s", 7200))

    def _databases(self) -> dict[str, Any]:
        h = self.h
        return {
            "trace": h.tracer.db,
            "session": h.session.db,
            "approvals": h.approvals.db,
            "budgets": h.budgets.db,
            "memory": h.memory_db,
            "evals": h.evals.store.db,
        }

    def candidates(self, now: float | None = None) -> list[str]:
        """Expired tenants, oldest expiry first, minus those with a turn running here."""
        now = self.clock() if now is None else now
        found = list(self.h.tenants.expired(now))
        cutoff = iso(now - self.ttl_s)
        rows = self.h.session.db.query(
            "SELECT tenant_id, MIN(created_at) AS first FROM sessions"
            " WHERE tenant_id LIKE 'anon-%' GROUP BY tenant_id HAVING first < ?",
            (cutoff,),
        )
        for row in rows:
            tenant = row["tenant_id"]
            expires = self.h.tenants.expires_at(tenant)
            if tenant not in found and (expires is None or expires <= now):
                found.append(tenant)
        busy = self.h.gateway.running_tenants()
        return [t for t in found if TENANT_ID.match(t) and t not in busy]

    def purge(self, tenant_id: str) -> dict[str, int]:
        """Delete every row of one tenant. Returns the rows deleted per store."""
        if not (TENANT_ID.match(tenant_id) or SCRATCH.match(tenant_id)):
            raise ValueError(f"refusing to sweep tenant {tenant_id!r}")
        counts: dict[str, int] = {}
        evals = self.h.evals.store.db
        runs = [r["run_id"] for r in evals.query(
            "SELECT run_id FROM eval_runs WHERE tenant_id = ?", (tenant_id,)
        )]  # fmt: skip
        for run_id in runs:
            counts["evals.eval_cases"] = (
                counts.get("evals.eval_cases", 0)
                + evals.execute("DELETE FROM eval_cases WHERE run_id = ?", (run_id,)).rowcount
            )
            scratch = scratch_tenant(run_id)
            if SCRATCH.match(scratch):
                for key, n in self.purge(scratch).items():
                    counts[key] = counts.get(key, 0) + n
        for name, db in self._databases().items():
            for table, column in _tenant_tables(db):
                n = db.execute(f"DELETE FROM {table} WHERE {column} = ?", (tenant_id,)).rowcount
                if n:
                    counts[f"{name}.{table}"] = counts.get(f"{name}.{table}", 0) + n
        if self.h.vectors.backend != "sqlite":  # the sqlite index is a table of the memory file
            n = self.h.vectors.delete_scope(tenant_id)
            if n:
                counts["vectors"] = counts.get("vectors", 0) + n
        n = self.h.audit.purge_tenant(tenant_id)
        if n:
            counts["audit"] = counts.get("audit", 0) + n
        workdir = self.h.sandbox.workdir / SAFE_NAME.sub("_", tenant_id)
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
            counts["sandbox_dirs"] = counts.get("sandbox_dirs", 0) + 1
        self._forget_in_memory(tenant_id)
        self.h.tenants.forget(tenant_id)
        return counts

    def _forget_in_memory(self, tenant_id: str) -> None:
        h = self.h
        h.gateway.forget_tenant(tenant_id)
        for view in [h, *h.packs.values()]:
            with contextlib.suppress(Exception):
                view.model_layer.last_route.pop(tenant_id, None)
                view.context_builder.last.pop(tenant_id, None)

    def sweep(self, now: float | None = None) -> dict[str, Any]:
        """One pass: every expired tenant, and what was deleted."""
        tenants = self.candidates(now)
        rows: dict[str, int] = {}
        for tenant in tenants:
            for key, n in self.purge(tenant).items():
                rows[key] = rows.get(key, 0) + n
        return {"tenants": tenants, "rows": rows}


def _tenant_tables(db: Any) -> list[tuple[str, str]]:
    """Every ordinary table of a store file keyed by tenant: (table, column), where the column
    is `tenant_id`, or `scope` for the tables that scope rows to a tenant or the shared pack
    (vectors, knowledge documents, chunks and their full-text tables). Audit is excluded: it
    goes through `AuditLog.purge_tenant`."""
    tables = [
        r["name"]
        for r in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    out = []
    for table in tables:
        if table == "audit" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            continue
        columns = {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
        if "tenant_id" in columns:
            out.append((table, "tenant_id"))
        elif "scope" in columns:
            out.append((table, "scope"))
    return out
