"""Helpers for the public-mode tests (P6): a public harness on the fake provider, optionally a
mocked Anthropic provider, a priced fake alias for the budget, and an HTTP client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from harnesswalk.config.loader import Settings, load_settings
from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT

SECRET = "public-test-secret"
SITE_KEY_ENV = "HARNESSWALK_TEST_SITE_KEY"  # the site's Anthropic key variable in these tests

# A fake alias with a price, so the shared budget can be spent without a hosted model:
# one cent per input token, so a call with `usage.input_tokens: 1` costs exactly $0.01 (under
# the loop's own per-turn dollar budget).
PRICED = {
    "provider": "fake",
    "label": "Priced fake",
    "context_tokens": 32000,
    "price_in_per_mtok": 10_000,
    "price_out_per_mtok": 0,
    "tools": True,
}


def public_settings(
    tmp_path: Path,
    fixture: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    fixtures: Path | None = None,
    hosted: bool = False,
    priced: bool = False,
    env: dict[str, str] | None = None,
    profile: Path | None = None,
) -> Settings:
    """Public-mode settings. `hosted` builds the Anthropic provider (mock it with
    `mock_anthropic`) next to the fake one; `priced` puts the priced fake alias first."""
    extra: dict[str, Any] = {"sandbox.level": "inprocess", "public.sweep_interval_s": 0}
    extra["models.providers.anthropic.api_key_env"] = SITE_KEY_ENV
    if fixtures is not None:
        extra["models.providers.fake.fixture_dir"] = str(fixtures)
    if priced:
        extra["models.models.fake-priced"] = dict(PRICED)
        extra["public.models"] = ["fake-priced", "fake-small"]
        extra["public.default_model"] = "fake-priced"
    extra.update(overrides or {})
    return load_settings(
        ROOT,
        mode="public",
        provider=None if hosted else "fake",
        fixture=fixture,
        profile=profile,
        overrides=extra,
        data_dir=tmp_path / "data",
        env={"HARNESSWALK_SECRET": SECRET, **(env or {})},
    )


def public_harness(tmp_path: Path, fixture: str | None = None, **kwargs: Any) -> Harness:
    return Harness(public_settings(tmp_path, fixture, **kwargs))


def client(h: Harness, app: Any = None, **kwargs: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app or create_app(h))
    return httpx.AsyncClient(transport=transport, base_url="http://test", **kwargs)


def anthropic_reply(text: str = "Hello from the mocked model.", tokens: int = 100) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": tokens, "output_tokens": 20},
    }


def mock_anthropic(h: Harness, handler: Any = None) -> list[httpx.Request]:
    """Answer the Anthropic provider's calls in-process; returns the requests it saw."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(200, json=anthropic_reply())

    h.providers["anthropic"].transport = httpx.MockTransport(respond)  # type: ignore[attr-defined]
    return seen


async def run(c: httpx.AsyncClient, h: Harness, body: dict, **kwargs: Any) -> dict:
    """POST a turn, wait for it and return {response, events} (events of the turn)."""
    r = await c.post("/v1/turns", json=body, **kwargs)
    out: dict[str, Any] = {"response": r, "events": []}
    if r.status_code == 202:
        turn_id = r.json()["turn_id"]
        state = await h.gateway.wait(turn_id, 10)
        out["events"] = h.tracer.get_turn(state.ctx.tenant_id, turn_id)
        out["tenant"] = state.ctx.tenant_id
    return out


def node_exits(events: list[dict], node_id: str) -> list[dict]:
    return [e for e in events if e["type"] == "node_exit" and e.get("node_id") == node_id]


def every_file_bytes(root: Path) -> list[tuple[Path, bytes]]:
    return [(p, p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
