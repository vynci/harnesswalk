"""Memory writer: the single gated write path into semantic and episodic memory.

Contract: spec/contracts/memory_writer.md. Node: `memory_writer` (context plane, kind code).

Proposals arrive from three places:

- the loop: the model calls the `remember` tool (typed arguments, action class
  `memory.write`, checked by policy like every tool call); the loop turns each successful
  call into a proposal;
- the turn itself: one short `turn` event per finished chat turn for episodic memory
  (`memory_writer.auto_episodes`, only when `episodic` is among `context.stores`);
- the ops seam: `propose()` and `process()`, which eval diagnosis uses in P4.

In a chat turn the writer runs after the gateway reply and before `trace`, and only when
there are proposals, so a slow gate never delays the answer. For each proposal it emits
`memory_proposed`, then gates it:

    disabled                 dropped, counted as rejected (no events)
    wrong tenant, bad shape  rejected
    over max_proposals       rejected (model proposals only; the turn event always counts)
    policy memory.write      the first matching rule decides; deny rejects, needs_approval
                             sends it to review
    public mode              gate `session`: held for the session only, never written
    gate auto                dev, or private for tenants in auto_approve_tenants ("*" = all)
    gate eval                deterministic: duplicates and contradictions are rejected
    gate review              pending until a human decides (POST /v1/memory/proposals/{id})

An accepted proposal is written with the writer token, versioned (a fact update keeps the
previous version, diffable) and reported with `memory_written`. Every string in a proposal
passes PII redaction before it is stored.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.context.records import PACK_SCOPE
from harnesswalk.context.stores.episodic import EpisodicStore
from harnesswalk.context.stores.semantic import SemanticStore, normalize_key
from harnesswalk.control.pii import PiiDetector
from harnesswalk.events import iso
from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ToolCall, TurnContext

register("memory_writer", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    store TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence TEXT NOT NULL,
    gate TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    record_id TEXT,
    version INTEGER,
    op TEXT,
    before TEXT,
    after TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);
CREATE INDEX IF NOT EXISTS memory_proposals_tenant ON memory_proposals (tenant_id, created_at);
"""

REMEMBER = "remember"
WRITER = "memory_writer"
_DEFAULT_PII = PiiDetector()


def redact(text: str, detector: PiiDetector | None = None) -> tuple[str, dict[str, int]]:
    """PII redaction for proposal content, with the input guardrails' detector (P3): emails,
    phones, Luhn-valid cards and national ids become typed placeholders (`[EMAIL]`, `[CARD]`).
    Returns the text and counts by kind."""
    detector = detector or _DEFAULT_PII
    findings = detector.find(text)
    return detector.redact(text, findings), detector.counts(findings)


@dataclass
class MemoryProposal:
    store: str  # semantic | episodic
    content: dict[str, Any]
    source: str = "loop"  # loop | turn | diagnose
    evidence: list[dict[str, Any]] = field(default_factory=list)
    user_id: str | None = None
    tenant_id: str | None = None  # when given it must be the turn's tenant


@dataclass
class ProposalRecord:
    proposal_id: str
    tenant_id: str
    store: str
    status: str  # pending | accepted | rejected
    gate: str  # review | eval | auto | session
    source: str = "loop"
    reason: str | None = None
    record_id: str | None = None
    version: int | None = None
    op: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class WriterReport:
    proposed: int = 0
    written: int = 0
    rejected: int = 0
    pending: int = 0
    records: list[ProposalRecord] = field(default_factory=list)


def remember_tool() -> ToolSpec:
    """The model's way to ask for a memory. Running it writes nothing: the call is a
    proposal, and the writer decides after the reply."""

    def remember(arguments: dict[str, Any]) -> str:
        what = "fact" if arguments["kind"] == "fact" else "event"
        return json.dumps(
            {"proposed": what, "note": "The memory writer decides after the reply."},
            sort_keys=True,
        )

    return ToolSpec(
        name=REMEMBER,
        description=(
            "Propose a durable memory. kind=fact needs key and value (a stable fact about the"
            " user or their orders); kind=event needs text (something that happened). The"
            " memory writer decides whether it is kept."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": ["fact", "event"]},
                "key": {"type": "string", "minLength": 1, "maxLength": 120},
                "value": {"type": "string", "minLength": 1, "maxLength": 500},
                "text": {"type": "string", "minLength": 1, "maxLength": 500},
            },
        },
        action_class="memory.write",
        timeout_ms=1000,
        impl=remember,
        pure=True,
    )


def proposal_from_call(ctx: TurnContext, call: ToolCall) -> MemoryProposal | None:
    """A successful `remember` call as a proposal (arguments were validated by tools)."""
    if call.name != REMEMBER:
        return None
    args = call.arguments
    evidence = [{"store": "session", "id": ctx.turn_id, "call_id": call.call_id}]
    if args.get("kind") == "fact":
        content = {"key": args.get("key", ""), "value": args.get("value", "")}
        return MemoryProposal("semantic", content, "loop", evidence, ctx.user_id)
    content = {"kind": "note", "text": args.get("text", "")}
    return MemoryProposal("episodic", content, "loop", evidence, ctx.user_id)


def policy_decision(policy: Any, ctx: TurnContext) -> tuple[str, str]:
    """What policy says about a memory write for this user in this mode: (decision, rule id).

    Policy's own decision for class `memory.write` (`Policy.decide_memory_write`): the same
    rules as tool calls, roles included; the first matching rule wins and no match is a deny.
    """
    if policy is None:
        return "allow", "none"
    if (not policy.enabled or ctx.is_disabled("policy")) and ctx.mode != "public":
        return "allow", "disabled"
    roles = list(ctx.identity.roles) if getattr(ctx, "identity", None) else ["user"]
    decision = policy.decide_memory_write(roles)
    return decision.decision, decision.rule


class MemoryWriter:
    def __init__(
        self,
        cfg: dict[str, Any],
        db: Database,
        semantic: SemanticStore,
        episodic: EpisodicStore,
        policy: Any = None,
        *,
        episodes_enabled: bool = True,
        pii: PiiDetector | None = None,
    ) -> None:
        self.cfg = cfg
        self.pii = pii or _DEFAULT_PII  # the input guardrails' detector, pack patterns included
        self.db = db
        self.semantic = semantic
        self.episodic = episodic
        self.policy = policy
        self.enabled = bool(cfg.get("enabled", True))
        self.gate = cfg.get("gate", "auto")
        if self.gate not in ("auto", "review", "eval"):
            from harnesswalk.types import ConfigError

            raise ConfigError(f"memory_writer.gate must be auto, review or eval: {self.gate}")
        self.auto_tenants = list(cfg.get("auto_approve_tenants", ["*"]) or [])
        self.max_per_turn = int(cfg.get("max_proposals_per_turn", 3))
        self.auto_episodes = bool(cfg.get("auto_episodes", True)) and episodes_enabled
        self._queued: dict[str, list[MemoryProposal]] = {}  # turn_id -> proposals (ops seam)

    # -- the ops seam --------------------------------------------------------------------------

    def propose(self, ctx: TurnContext, proposal: MemoryProposal) -> None:
        """Queue a proposal for this turn; `process(ctx)` gates it (P4 eval diagnosis)."""
        self._queued.setdefault(ctx.turn_id, []).append(proposal)

    async def process(self, ctx: TurnContext) -> WriterReport:
        return await self.run(ctx, self._queued.pop(ctx.turn_id, []))

    # -- the turn ------------------------------------------------------------------------------

    def turn_episode(
        self, ctx: TurnContext, user_text: str | None, reply: str, exit_: str
    ) -> list[MemoryProposal]:
        """The short `turn` event a finished chat turn proposes for episodic memory."""
        if not self.auto_episodes or user_text is None:
            return []
        text = f"Asked: {user_text.strip()[:160]} Answered: {reply.strip()[:200]}"
        content = {"kind": "turn", "text": text, "outcome": exit_, "turn_id": ctx.turn_id}
        evidence = [{"store": "session", "id": ctx.turn_id}]
        return [MemoryProposal("episodic", content, "turn", evidence, ctx.user_id)]

    def gate_for(self, ctx: TurnContext) -> str:
        if ctx.mode == "public":
            return "session"  # public memory never outlives the session (spec/harness.md 7)
        if self.gate != "auto":
            return self.gate
        if ctx.mode == "dev":
            return "auto"
        if "*" in self.auto_tenants or ctx.tenant_id in self.auto_tenants:
            return "auto"
        return "review"

    async def run(self, ctx: TurnContext, proposals: list[MemoryProposal]) -> WriterReport:
        report = WriterReport()
        if not proposals:
            return report
        ctx.emit.edge("loop_to_memory_writer")
        async with node(ctx, "memory_writer") as span:
            if not self.enabled or ctx.is_disabled("memory_writer"):
                report.proposed = report.rejected = len(proposals)
                span.skip(
                    disabled=True,
                    proposed=len(proposals),
                    written=0,
                    rejected=len(proposals),
                    pending=0,
                )
                return report
            gate = self.gate_for(ctx)
            decision, rule = policy_decision(self.policy, ctx)
            model_seen = 0
            for proposal in proposals:
                if proposal.source != "turn":
                    model_seen += 1
                record = self._one(ctx, proposal, gate, decision, rule, model_seen)
                report.proposed += 1
                report.records.append(record)
                if record.status == "accepted":
                    report.written += 1
                elif record.status == "rejected":
                    report.rejected += 1
                else:
                    report.pending += 1
            span.set(
                proposed=report.proposed,
                written=report.written,
                rejected=report.rejected,
                pending=report.pending,
                gate=gate,
                policy_rule=rule,
            )
            rejections = [
                {"proposal_id": r.proposal_id, "reason": r.reason}
                for r in report.records
                if r.status == "rejected"
            ]
            if rejections:
                span.set(rejections=rejections[:5])
            span.detail = {"records": [r.to_dict() for r in report.records]}
        return report

    def _one(
        self,
        ctx: TurnContext,
        proposal: MemoryProposal,
        gate: str,
        decision: str,
        rule: str,
        model_seen: int,
    ) -> ProposalRecord:
        content, redactions = self._clean(proposal.content)
        if decision == "needs_approval" and gate in ("auto", "eval"):
            gate = "review"
        record = ProposalRecord(
            "prop_" + uuid.uuid4().hex[:16],
            ctx.tenant_id,
            proposal.store,
            "pending",
            gate,
            proposal.source,
        )
        self._insert(ctx, proposal, content, record)
        preview = self._preview(proposal.store, content)
        ctx.emit.point(
            "memory_proposed",
            "memory_writer",
            "waiting",
            {
                "proposal_id": record.proposal_id,
                "store": proposal.store,
                "gate": gate,
                "preview": preview,
                "source": proposal.source,
                **({"redactions": redactions} if redactions else {}),
            },
        )
        reason = self._reject_reason(ctx, proposal, content, gate, decision, model_seen)
        if reason:
            return self._finish(record, "rejected", reason)
        if gate == "review":
            return record  # waits for a human decision
        written = self._write(ctx, proposal, content, record)
        if written.status == "accepted":
            ctx.emit.edge(f"memory_writer_to_{proposal.store}")
            ctx.emit.point(
                "memory_written",
                "memory_writer",
                "done",
                {
                    "proposal_id": record.proposal_id,
                    "store": proposal.store,
                    "record_id": written.record_id,
                    "version": written.version,
                    "op": written.op,
                },
            )
        return written

    # -- checks --------------------------------------------------------------------------------

    def _clean(self, content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
        out, counts = {}, {}
        for key, value in content.items():
            if isinstance(value, str) and key not in ("turn_id", "outcome", "ts"):
                value, found = redact(value, self.pii)
                for kind, n in found.items():
                    counts[kind] = counts.get(kind, 0) + n
            out[key] = value
        return out, counts

    @staticmethod
    def _preview(store: str, content: dict[str, Any]) -> str:
        text = (
            f"{content.get('key', '')}: {content.get('value', '')}"
            if store == "semantic"
            else str(content.get("text", ""))
        )
        return text if len(text) <= 120 else text[:117] + "..."

    def _reject_reason(self, ctx, proposal, content, gate, decision, model_seen) -> str | None:
        if proposal.tenant_id and proposal.tenant_id != ctx.tenant_id:
            return "cross_tenant"
        if ctx.tenant_id.startswith("_"):
            return "reserved_tenant"
        if proposal.store not in ("semantic", "episodic"):
            return "unknown_store"
        if proposal.store == "semantic":
            key, value = content.get("key"), content.get("value")
            if not (
                isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
            ):
                return "invalid: a fact needs key and value"
            if len(key) > 120 or len(value) > 500:
                return "invalid: too long"
        else:
            text = content.get("text")
            if not (isinstance(text, str) and text.strip()) or len(text) > 600:
                return "invalid: an event needs text"
        if proposal.source != "turn" and model_seen > self.max_per_turn:
            return "max_proposals_per_turn"
        if decision == "deny":
            return "policy"
        if gate == "session":
            return "public_mode"
        if proposal.store == "semantic":
            existing = self.semantic.find(ctx.tenant_id, proposal.user_id, content["key"])
            if existing and existing.value == content["value"]:
                return "duplicate"
            if gate == "eval" and existing:
                return "contradiction"
        elif content.get("kind") == "turn" and self.episodic.exists_for_turn(
            ctx.tenant_id, content.get("turn_id", ctx.turn_id), "turn"
        ):
            return "duplicate"
        return None

    # -- writes --------------------------------------------------------------------------------

    def _write(self, ctx, proposal, content, record) -> ProposalRecord:
        try:
            if proposal.store == "semantic":
                result = self.semantic.write(
                    ctx.tenant_id,
                    proposal.user_id,
                    content["key"],
                    content["value"],
                    writer=WRITER,
                    source=f"{proposal.source}:{ctx.turn_id}",
                    proposal_id=record.proposal_id,
                )
                record.record_id, record.version, record.op = (
                    result.record_id,
                    result.version,
                    result.op,
                )
                before, after = result.before, result.after
            else:
                record.record_id = self.episodic.append(
                    ctx.tenant_id,
                    writer=WRITER,
                    session_id=ctx.session_id,
                    turn_id=content.get("turn_id", ctx.turn_id),
                    kind=content.get("kind", "note"),
                    text=content["text"],
                    outcome=content.get("outcome", ""),
                    source=f"{proposal.source}:{ctx.turn_id}",
                    proposal_id=record.proposal_id,
                )
                record.version, record.op = 1, "create"
                before, after = None, content["text"]
        except PermissionError:
            raise  # a store refusing the writer token is a programming error
        except Exception as exc:  # a failed write leaves the proposal pending, never fails the turn
            record.reason = f"write_failed: {type(exc).__name__}"
            self.db.execute(
                "UPDATE memory_proposals SET reason = ? WHERE proposal_id = ?",
                (record.reason, record.proposal_id),
            )
            return record
        record.status = "accepted"
        self.db.execute(
            "UPDATE memory_proposals SET status = 'accepted', record_id = ?, version = ?, op = ?,"
            " before = ?, after = ?, decided_at = ?, decided_by = ? WHERE proposal_id = ?",
            (
                record.record_id,
                record.version,
                record.op,
                before,
                after,
                iso(time.time()),
                f"gate:{record.gate}",
                record.proposal_id,
            ),
        )
        return record

    def _insert(self, ctx, proposal, content, record) -> None:
        self.db.execute(
            "INSERT INTO memory_proposals (proposal_id, tenant_id, user_id, session_id, turn_id,"
            " store, content, source, evidence, gate, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.proposal_id,
                ctx.tenant_id,
                proposal.user_id,
                ctx.session_id,
                ctx.turn_id,
                proposal.store,
                json.dumps(content, sort_keys=True),
                proposal.source,
                json.dumps(proposal.evidence, sort_keys=True),
                record.gate,
                "pending",
                iso(time.time()),
            ),
        )

    def _finish(self, record: ProposalRecord, status: str, reason: str) -> ProposalRecord:
        record.status, record.reason = status, reason
        self.db.execute(
            "UPDATE memory_proposals SET status = ?, reason = ?, decided_at = ?, decided_by = ?"
            " WHERE proposal_id = ?",
            (status, reason, iso(time.time()), f"gate:{record.gate}", record.proposal_id),
        )
        return record

    # -- review and the inspector --------------------------------------------------------------

    def _row(self, row: Any) -> dict[str, Any]:
        out = dict(row)
        out["content"] = json.loads(out["content"])
        out["evidence"] = json.loads(out["evidence"])
        return out

    def get(self, tenant_id: str, proposal_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT * FROM memory_proposals WHERE tenant_id = ? AND proposal_id = ?",
            (tenant_id, proposal_id),
        )
        return self._row(row) if row else None

    def pending(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM memory_proposals WHERE tenant_id = ? AND status = 'pending'"
            " AND gate = 'review' ORDER BY created_at DESC, proposal_id LIMIT ?",
            (tenant_id, limit),
        )
        return [self._row(r) for r in rows]

    def recent(self, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """The last proposals with their outcome, and for writes a diff (before, after)."""
        rows = self.db.query(
            "SELECT * FROM memory_proposals WHERE tenant_id = ?"
            " ORDER BY created_at DESC, proposal_id DESC LIMIT ?",
            (tenant_id, limit),
        )
        out = []
        for r in rows:
            item = self._row(r)
            if item["status"] == "accepted":
                item["diff"] = {"op": item["op"], "before": item["before"], "after": item["after"]}
            out.append(item)
        return out

    def history(self, tenant_id: str, record_id: str) -> list[dict[str, Any]]:
        return self.semantic.history(tenant_id, record_id)

    def decide(
        self,
        tenant_id: str,
        proposal_id: str,
        decider: str,
        decision: str,
        mode: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """A human decision on a proposal waiting at the review gate. Decided at most once.

        Raises KeyError for an unknown proposal, ValueError when it is not pending review or
        when the mode forbids writes (public)."""
        row = self.get(tenant_id, proposal_id)
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] != "pending" or row["gate"] != "review":
            raise ValueError("this proposal is not waiting for review")
        if mode == "public":
            raise ValueError("public mode never writes durable memory")
        if decision not in ("accept", "reject"):
            raise ValueError("decision must be accept or reject")
        now = iso(time.time())
        if decision == "reject":
            self.db.execute(
                "UPDATE memory_proposals SET status = 'rejected', reason = ?, decided_at = ?,"
                " decided_by = ? WHERE proposal_id = ?",
                (reason or "rejected by review", now, decider, proposal_id),
            )
            return self.get(tenant_id, proposal_id) or {}
        content = row["content"]
        if row["store"] == "semantic":
            result = self.semantic.write(
                tenant_id,
                row["user_id"],
                content["key"],
                content["value"],
                writer=WRITER,
                source=f"review:{decider}",
                proposal_id=proposal_id,
            )
            record_id, version, op = result.record_id, result.version, result.op
            before, after = result.before, result.after
        else:
            record_id = self.episodic.append(
                tenant_id,
                writer=WRITER,
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                kind=content.get("kind", "note"),
                text=content["text"],
                outcome=content.get("outcome", ""),
                source=f"review:{decider}",
                proposal_id=proposal_id,
            )
            version, op, before, after = 1, "create", None, content["text"]
        self.db.execute(
            "UPDATE memory_proposals SET status = 'accepted', record_id = ?, version = ?, op = ?,"
            " before = ?, after = ?, decided_at = ?, decided_by = ?, reason = ?"
            " WHERE proposal_id = ?",
            (record_id, version, op, before, after, now, decider, reason, proposal_id),
        )
        return self.get(tenant_id, proposal_id) or {}

    # -- pack seeds ----------------------------------------------------------------------------

    def seed(self, pack_dir: Any) -> dict[str, int]:
        """Load the pack's `facts.jsonl` and `episodes.jsonl` into the shared pack scope.

        Seeds are pack content, released with the pack like its documents; they still go
        through this writer (the only holder of the writer token) and are versioned. Running
        it twice changes nothing unless the files changed.
        """
        facts = episodes = 0
        facts_file = pack_dir / "facts.jsonl"
        if facts_file.exists():
            for line in facts_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                key, value = normalize_key(item["key"]), str(item["value"])
                result = self.semantic.write(
                    PACK_SCOPE,
                    None,
                    key,
                    value,
                    writer=WRITER,
                    source=f"seed:{self.semantic.pack}",
                    extra_entities=item.get("entities"),
                )
                facts += result.op != "unchanged"
        episodes_file = pack_dir / "episodes.jsonl"
        if episodes_file.exists():
            existing = self.db.query(
                "SELECT turn_id FROM episodes WHERE tenant_id = ? AND pack = ?",
                (PACK_SCOPE, self.episodic.pack),
            )
            seen = {r["turn_id"] for r in existing}
            for line in episodes_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                seed_id = "seed:" + item["id"]
                if seed_id in seen:  # events are append-only: a seed is loaded once
                    continue
                self.episodic.append(
                    PACK_SCOPE,
                    writer=WRITER,
                    session_id="seed",
                    turn_id=seed_id,
                    kind=item.get("kind", "event"),
                    text=item["text"],
                    outcome=item.get("outcome", ""),
                    ts=item["ts"],
                    source=f"seed:{self.episodic.pack}",
                    extra_entities=item.get("entities"),
                )
                episodes += 1
        return {"facts": facts, "episodes": episodes}
