"""Shared records from spec/contracts/README.md ("Shared types").

Plain dataclasses, so every component can be read without knowing a framework. Field names
follow the spec in snake_case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from harnesswalk.events import TurnEmitter

Mode = Literal["dev", "public", "private"]
ActionClass = Literal["read", "write", "destructive", "external", "memory.write"]
ACTION_CLASSES = ("read", "write", "destructive", "external", "memory.write")


class HarnessError(Exception):
    """An error with a code from spec/events.md section 4. It closes the span it escapes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(Exception):
    """The configuration cannot be loaded. The harness refuses to start."""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Observation:
    call_id: str
    outcome: Literal["ok", "denied", "error", "timeout"]
    content: str
    limit: str | None = None  # a loop exit this observation forces (budget_tool_calls)


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            out["tool_calls"] = [
                {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                for c in self.tool_calls
            ]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        calls = [ToolCall(**c) for c in data.get("tool_calls", [])]
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=calls,
        )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.usd += other.usd

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost(self) -> dict[str, Any]:
        """The `cost` object of an event: tokens and dollars, nothing else."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": round(self.usd, 8),
        }


@dataclass
class Identity:
    tenant_id: str
    user_id: str | None
    roles: list[str]
    anonymous: bool
    expires_at: float | None = None  # P6: when an anonymous public tenant is deleted (Unix time)


@dataclass
class FinalAnswer:
    text: str
    exit_reason: str
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TurnContext:
    """Everything a component needs to know about the turn it works for.

    `tenant_id` is required on every call that touches data. `emit` is the only way a
    component reports what it did.
    """

    tenant_id: str
    session_id: str
    turn_id: str
    trace_id: str
    user_id: str | None
    mode: Mode
    emit: TurnEmitter
    turn_index: int = 0
    identity: Identity | None = None
    disabled_nodes: frozenset[str] = frozenset()
    started_mono: float = field(default_factory=time.monotonic)
    interrupted: bool = False  # set by a cancel; the loop and the pause points check it
    calls_issued: int = 0  # for call_<n> ids assigned by the model layer
    # Per-turn provider choice (dev mode, http_api.md `provider` and `fixture`): the only
    # providers this turn may use. None means the harness's configured providers.
    providers: dict[str, Any] | None = None
    # This turn's PII placeholders (control/pii.py Vault). Memory only, never serialized.
    pii_vault: Any = field(default=None, repr=False)
    model_calls: int = 0  # completed model_layer calls this turn (the fake provider's cursor)
    model_hint: str | None = None  # a router alias hint (dev mode, http_api.md P3)
    budget: Any = None  # control/budgets.py TurnBudget: the tenant's limits and usage
    detached: bool = False  # the process is shutting down and leaves this turn to recovery
    # The knowledge chunks the context builder numbered for this turn ([n] -> doc and chunk),
    # so the reply's citation markers can be checked against them.
    citations: list[dict[str, Any]] = field(default_factory=list)
    recalled: list[str] = field(default_factory=list)  # semantic facts in context (P5)
    # P4 checkpoints: what a turn re-attached after a restart must keep. The structured reply
    # schema it asked for, and whether a BYO key was present (never the key itself).
    output_schema: dict[str, Any] | None = None
    byo_key: bool = False
    # P6: notices the walk raised for the reply ([{reason, message}]), such as a turn that fell
    # through to the fake model when the shared hosted budget ran out (`budget_exhausted`).
    notices: list[dict[str, str]] = field(default_factory=list)

    def notice(self, reason: str, message: str) -> bool:
        """Add a notice once per reason. True when it is new."""
        if any(n["reason"] == reason for n in self.notices):
            return False
        self.notices.append({"reason": reason, "message": message})
        return True

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required on every TurnContext")

    def active_seconds(self) -> float:
        """Wall-clock time of the turn, excluding time spent paused."""
        return time.monotonic() - self.started_mono - self.emit.paused_s

    def is_disabled(self, node_id: str) -> bool:
        return node_id in self.disabled_nodes
