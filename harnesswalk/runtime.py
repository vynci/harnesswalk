"""Wiring: build every component from the settings and connect them.

Nothing clever happens here. Each component gets its configuration section and the
collaborators its contract names, and the trace store subscribes to the event bus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harnesswalk import SPEC_VERSION, __version__
from harnesswalk.config.loader import Settings
from harnesswalk.context.builder import ContextBuilder
from harnesswalk.context.stores import Memory
from harnesswalk.context.stores.procedural import ProceduralStore
from harnesswalk.context.stores.session import SessionStore, Turn
from harnesswalk.control.approval import ApprovalQueue
from harnesswalk.control.audit import AuditLog
from harnesswalk.control.gateway import Gateway
from harnesswalk.control.input_guardrails import InputGuardrails
from harnesswalk.control.policy import Policy
from harnesswalk.events import EventBus
from harnesswalk.execution.builtin_tools import CALCULATOR, read_skill_tool
from harnesswalk.execution.loop import Loop
from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
from harnesswalk.execution.output_guardrails import OutputGuardrails
from harnesswalk.execution.providers import (
    PROVIDER_NAMES,
    ModelProvider,
    ProviderError,
    build_provider,
    build_providers,
)
from harnesswalk.execution.providers.fake import (
    FakeProvider,
    find_fixture,
    fixture_dirs,
    list_fixtures,
    load_fixture,
)
from harnesswalk.execution.registry import ToolRegistry
from harnesswalk.execution.router import Router, RouteRequest
from harnesswalk.execution.sandbox import Sandbox
from harnesswalk.execution.tools import Tools
from harnesswalk.nodes import load_components
from harnesswalk.ops.trace import Tracer
from harnesswalk.types import ConfigError, Message, TurnContext


class Harness:
    def __init__(self, settings: Settings) -> None:
        self.registrations = load_components()
        self.settings = s = settings
        mode = s.mode

        self.bus = EventBus()
        self.tracer = Tracer(str(s.resolve(s.get("trace.sqlite_path", "data/trace.db"))))
        self.bus.subscribe(self.tracer.record)

        self.session = SessionStore(
            str(s.resolve(s.get("stores.session.sqlite_path", "data/session.db")))
        )
        self.audit = AuditLog(str(s.resolve(s.get("audit.sqlite_path", "data/audit.db"))))
        self.approvals = ApprovalQueue(
            str(s.resolve(s.get("approval.sqlite_path", "data/approvals.db"))),
            timeout_s=float(s.get("approval.timeout_s", 900)),
            show_args=bool(s.get("approval.show_args", True)),
        )
        self.procedural = ProceduralStore.from_settings(s)

        self.registry = ToolRegistry()
        self._register_tools()
        self.policy = Policy(
            s.get("policy", {}),
            mode,
            record_read_allows=bool(s.get("audit.record_read_allows", False)),
        )
        self.policy.check_public_mode(self.registry.all())
        self.sandbox = Sandbox(
            s.get("sandbox.level", "inprocess"), int(s.get("sandbox.max_timeout_ms", 10000))
        )
        self.tools = Tools(
            self.registry,
            self.policy,
            self.audit,
            self.approvals,
            self.sandbox,
            max_observation_chars=int(s.get("tools.max_observation_chars", 4000)),
        )

        self.providers = build_providers(s)
        if not self.providers:
            raise ConfigError("no model provider is configured (set HARNESSWALK_PROVIDER)")
        models = s.get("models.models", {})
        self.router = Router(
            s.get("models.router.rules", []), models, lambda p: p in self.providers
        )
        self.model_layer = ModelLayer(
            self.providers,
            self.router,
            models,
            timeout_s=float(s.get("models.model_layer.timeout_s", 60)),
            max_output_tokens=int(s.get("models.model_layer.max_output_tokens", 1024)),
            capture_content=bool(s.get("trace.capture_content", True)),
        )

        estimator = s.get("context.token_estimator", "chars_div_4")
        self.memory = Memory(self.procedural, self.session, estimator=estimator)
        self.context_builder = ContextBuilder(
            s.get("context", {}), self.memory, self.session, summarizer=self._summarize
        )
        self.loop = Loop(self.model_layer, self.tools, self.session, s.get("loop.notices", {}))
        self.input_guardrails = InputGuardrails(s.get("input_guardrails", {}), mode)
        self.output_guardrails = OutputGuardrails(s.get("output_guardrails", {}), mode)
        self.gateway = Gateway(self)
        self._turn_providers: dict[str, ModelProvider] = {}  # built on demand for a turn
        self._turn_fakes: dict[str, FakeProvider] = {}  # fixture name -> provider
        self._startup_fake: FakeProvider | None = None  # `provider: fake` on another server

    def _register_tools(self) -> None:
        s = self.settings
        builtins = s.get("tools.builtins", ["calculator", "read_skill"])
        if "calculator" in builtins:
            self.registry.register(CALCULATOR)
        if "read_skill" in builtins:
            self.registry.register(
                read_skill_tool(self.procedural.skill_body, sorted(self.procedural.skills))
            )
        files = s.get("tools.files", None)
        if files is None:
            pack_tools = Path("datasets") / s.get("dataset", "_starter") / "tools.yaml"
            files = [str(pack_tools)] if (s.root / pack_tools).exists() else []
        for rel in files:
            self.registry.load_yaml(s.resolve(rel))

    @property
    def provider_name(self) -> str:
        """The provider the default route lands on, as /healthz reports it."""
        try:
            route = self.router.route(
                RouteRequest(step=1, step_kind="first", mode=self.settings.mode)
            )
        except ProviderError:
            return next(iter(self.providers))
        return self.settings.get(f"models.models.{route.model}.provider")

    # -- per-turn provider choice (dev mode) -------------------------------------------------

    def provider_available(self, name: str) -> bool:
        """Whether a turn could run on this provider (`configured` in /v1/capabilities)."""
        if name in self.providers:
            return True
        if name == "fake":
            return self.settings.mode == "dev"
        return self.settings.provider_prerequisites(name)

    def fixtures(self) -> list[str]:
        return list_fixtures(fixture_dirs(self.settings))

    def turn_providers(self, provider: str | None, fixture: str | None) -> dict[str, ModelProvider]:
        """The providers one turn may use when it picked a provider or a fixture.

        A fixture loads its own fake provider, cached per fixture and counting turns per
        session on that fixture (http_api.md). `provider: fake` without a fixture runs the
        start-up fixture with the plain rule (turn `n` of the session reads `turns[n]`), the
        same as on a server started with the fake provider. Raises ConfigError for an unknown
        fixture.
        """
        if fixture is not None:
            fake = self._turn_fakes.get(fixture)
            if fake is None:
                loaded = load_fixture(find_fixture(fixture, fixture_dirs(self.settings)))
                fake = self._turn_fakes[fixture] = FakeProvider(loaded, relative_turns=True)
            return {"fake": fake}
        assert provider is not None
        if provider in self.providers:
            return {provider: self.providers[provider]}
        if provider == "fake":
            if self._startup_fake is None:
                name = self.settings.fixture() or "plain_answer"
                loaded = load_fixture(find_fixture(name, fixture_dirs(self.settings)))
                self._startup_fake = FakeProvider(loaded)
            return {"fake": self._startup_fake}
        if provider not in self._turn_providers:
            self._turn_providers[provider] = build_provider(provider, self.settings)
        return {provider: self._turn_providers[provider]}

    def capabilities(self) -> dict[str, Any]:
        """GET /v1/capabilities: what this build can do, so the UI adapts (http_api.md)."""
        s, mode = self.settings, self.settings.mode
        models = s.get("models.models", {})
        default = self.provider_name
        providers = [
            {
                "name": name,
                "configured": self.provider_available(name),
                "default": name == default,
                "models": [
                    alias
                    for alias, cfg in models.items()
                    if cfg.get("provider") == name and not cfg.get("embedding")
                ],
            }
            for name in PROVIDER_NAMES
        ]
        return {
            "mode": mode,
            "version": __version__,
            "spec_version": SPEC_VERSION,
            "providers": providers,
            "fixtures": self.fixtures() if mode == "dev" else [],
            "features": {
                "step_mode": True,
                "approvals": True,
                "message_delta": int(s.get("gateway.stream_reply_chars", 0)) > 0,
                "cancel": True,
                "byo_key": mode in ("public", "private"),
                "disable_nodes": mode != "public",
            },
            "limits": {
                "max_message_chars": int(s.get("gateway.max_message_chars", 32000)),
                "max_steps": int(s.get("loop.max_steps", 8)),
                "session_ttl_seconds": (
                    int(s.get("gateway.session_ttl_s", 7200)) if mode == "public" else None
                ),
            },
        }

    async def _summarize(self, ctx: TurnContext, previous: str, turns: list[Turn]) -> str | None:
        """Compaction by a model, when the router has a non-fake summarize model."""
        try:
            route = self.model_layer.router_for(ctx).route(
                RouteRequest(step=0, step_kind="summarize", tools_enabled=False, mode=ctx.mode)
            )
        except ProviderError:
            return None
        if self.settings.get(f"models.models.{route.model}.provider") == "fake":
            return None
        transcript = "\n".join(f"User: {t.user}\nAssistant: {t.assistant}" for t in turns)
        request = ModelRequest(
            messages=[
                Message(
                    "system",
                    "Summarize this conversation in a few short sentences."
                    " Keep names, ids and decisions.",
                ),
                Message(
                    "user", (f"Earlier summary:\n{previous}\n\n" if previous else "") + transcript
                ),
            ],
            tools=[],
            step=0,
            step_kind="summarize",
        )
        try:
            response = await self.model_layer.complete(ctx, request)
        except ProviderError:
            return None
        return response.text

    def health(self) -> dict[str, Any]:
        ok = self.tracer.db.ping()
        out: dict[str, Any] = {
            "status": "ok" if ok else "unavailable",
            "mode": self.settings.mode,
            "provider": self.provider_name,
            "checks": {"trace_store": "ok" if ok else "unavailable", "provider": "ok"},
        }
        fake = self.providers.get("fake")
        if fake is not None:
            out["fixture"] = fake.fixture_name  # type: ignore[attr-defined]
        return out

    async def aclose(self) -> None:
        await self.gateway.shutdown()
        for db in (self.tracer.db, self.session.db, self.audit.db, self.approvals.db):
            db.close()
