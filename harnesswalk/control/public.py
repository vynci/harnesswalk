"""Public mode (P6): what a server on the open internet needs on top of the gateway.

Not a node: the gateway, the HTTP surface and the model layer call into it
(spec/contracts/http_api.md, router.md and gateway.md, "P6 additions").

- `TenantCookies`: the anonymous tenant per browser, `anon-<hex>.<expires>.<hmac>` in an
  HTTP-only cookie signed with `HARNESSWALK_SECRET`. A tampered, foreign or expired cookie
  gets a fresh tenant, never an error.
- `client_ip`: the raw peer address, or with `gateway.trust_proxy` (env
  `HARNESSWALK_TRUST_PROXY=1`) `X-Harnesswalk-Viewer-Ip` (the distribution's viewer-ip function
  overwrites whatever the viewer sent), else the first `X-Forwarded-For` hop (Caddy writes a
  single one). Never `CloudFront-Viewer-Address`, which a viewer can send through the
  distribution.
- `rate_key`: the bucket an address counts in: IPv4 as is, IPv6 by its /64 (one subscriber's
  prefix), so rotating addresses inside one prefix does not buy more turns or streams.
- `RateLimiter`: the per-IP and per-tenant hourly windows, in a table bounded by
  `public.rate_limit_max_keys` (the least recently used key is evicted).
- `TenantRegistry`: when each anonymous tenant expires, so the sweeper (`ops/sweep.py`) knows
  whose rows to delete.
- `PublicModels`: the allowlist `public.models` and whether each alias can answer right now:
  its provider is built, a hosted provider has the site's key or the turn brings its own, the
  provider is healthy, and a priced alias still fits the shared daily budget (`ops/budget.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.config.loader import HOSTED_PROVIDERS, Settings
from harnesswalk.events import iso

TENANT_PREFIX = "anon-"
TENANT_ID = re.compile(r"^anon-[0-9a-f]{16}$")
WINDOW_S = 3600

# Reasons an alias is unavailable (GET /v1/capabilities `public.models[].reason`).
BUDGET_EXHAUSTED = "budget_exhausted"
PROVIDER_UNCONFIGURED = "provider_unconfigured"
PROVIDER_UNHEALTHY = "provider_unhealthy"


class TenantCookies:
    """Sign and check the anonymous tenant cookie. `clock` is injectable for tests."""

    def __init__(self, secret: str, ttl_s: float, clock: Callable[[], float] = time.time) -> None:
        if not secret:
            raise ValueError("a tenant cookie needs a secret")
        self._key = secret.encode()
        self.ttl_s = float(ttl_s)
        self.clock = clock

    def _sign(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:32]

    def mint(self) -> tuple[str, float, str]:
        """A fresh tenant: (tenant_id, expires_at, cookie value)."""
        tenant = TENANT_PREFIX + secrets.token_hex(8)
        expires = float(int(self.clock() + self.ttl_s))
        payload = f"{tenant}.{int(expires)}"
        return tenant, expires, f"{payload}.{self._sign(payload)}"

    def check(self, raw: str | None) -> tuple[str, float] | None:
        """(tenant_id, expires_at) for a valid, unexpired cookie, else None."""
        if not raw or len(raw) > 200:
            return None
        parts = raw.split(".")
        if len(parts) != 3:
            return None
        tenant, expires, sig = parts
        if not TENANT_ID.match(tenant) or not expires.isdigit():
            return None
        if not hmac.compare_digest(sig, self._sign(f"{tenant}.{expires}")):
            return None
        if float(expires) <= self.clock():
            return None
        return tenant, float(expires)


def client_ip(headers: Mapping[str, str], peer: str, trust_proxy: bool) -> str:
    """The address rate limits count. Proxy headers only count when the proxy is trusted.

    Behind the trusted proxy the address is `X-Harnesswalk-Viewer-Ip`, which the CloudFront
    viewer-ip function sets from the connection (overwriting any value the viewer sent), else
    the first `X-Forwarded-For` hop, the single one Caddy writes from that header.
    `CloudFront-Viewer-Address` never counts: the origin request policy forwards a viewer's own
    (P6 review).
    """
    if trust_proxy:
        viewer = (headers.get("x-harnesswalk-viewer-ip") or "").strip()
        if viewer:
            return viewer[:64]
        forwarded = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return peer


def rate_key(ip: str) -> str:
    """The rate-limit bucket of an address: IPv4 itself, IPv6 its /64 prefix."""
    try:
        addr = ipaddress.ip_address(ip.strip().strip("[]").split("%")[0])
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((addr, 64), strict=False))
    return str(addr)


def request_is_https(headers: Mapping[str, str], scheme: str, trust_proxy: bool) -> bool:
    if scheme == "https":
        return True
    return trust_proxy and (headers.get("x-forwarded-proto") or "").lower().startswith("https")


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


class RateLimiter:
    """Hourly sliding windows per key, at most `max_keys` keys (least recently used evicted)."""

    def __init__(self, max_keys: int = 10000, clock: Callable[[], float] = time.time) -> None:
        self.max_keys = max(1, int(max_keys))
        self.clock = clock
        self._windows: OrderedDict[str, list[float]] = OrderedDict()

    def _window(self, key: str, now: float) -> list[float]:
        window = [t for t in self._windows.get(key, []) if now - t < WINDOW_S]
        return window

    def hit(self, key: str, per_hour: int) -> int:
        """Count one request. Returns how many are left; RateLimited when none were."""
        now = self.clock()
        window = self._window(key, now)
        if len(window) >= per_hour:
            self._store(key, window)
            retry = int(WINDOW_S - (now - window[0])) + 1 if window else WINDOW_S
            raise RateLimited(max(1, retry))
        window.append(now)
        self._store(key, window)
        return per_hour - len(window)

    def remaining(self, key: str, per_hour: int) -> int:
        """What `hit` would leave, without counting a request."""
        return max(0, per_hour - len(self._window(key, self.clock())))

    def _store(self, key: str, window: list[float]) -> None:
        self._windows[key] = window
        self._windows.move_to_end(key)
        while len(self._windows) > self.max_keys:
            self._windows.popitem(last=False)

    def forget(self, key: str) -> None:
        self._windows.pop(key, None)

    def __len__(self) -> int:
        return len(self._windows)


TENANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS public_tenants (
    tenant_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS public_tenants_by_expiry ON public_tenants (expires_at);
"""


class TenantRegistry:
    """When each anonymous tenant expires. Lives in the session store's file."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        db.executescript(TENANTS_SCHEMA)

    def register(self, tenant_id: str, expires_at: float) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO public_tenants VALUES (?, ?, ?)",
            (tenant_id, self.clock(), float(expires_at)),
        )

    def expired(self, now: float | None = None) -> list[str]:
        now = self.clock() if now is None else now
        rows = self.db.query(
            "SELECT tenant_id FROM public_tenants WHERE expires_at <= ? ORDER BY expires_at",
            (now,),
        )
        return [r["tenant_id"] for r in rows]

    def forget(self, tenant_id: str) -> None:
        self.db.execute("DELETE FROM public_tenants WHERE tenant_id = ?", (tenant_id,))

    def expires_at(self, tenant_id: str) -> float | None:
        row = self.db.one("SELECT expires_at FROM public_tenants WHERE tenant_id = ?", (tenant_id,))
        return float(row["expires_at"]) if row else None


def metered(model_cfg: Mapping[str, Any]) -> bool:
    """An alias the shared daily budget pays for: it has a price."""
    return (
        float(model_cfg.get("price_in_per_mtok") or 0) > 0
        or float(model_cfg.get("price_out_per_mtok") or 0) > 0
    )


class PublicModels:
    """The public allowlist and whether each alias can answer now (capabilities, gateway,
    router). Reads the harness's live providers, health and budget on every call."""

    def __init__(self, harness: Any) -> None:
        self.h = harness

    @property
    def settings(self) -> Settings:
        return self.h.settings

    def aliases(self) -> list[str]:
        return self.settings.public_models()

    def allowed(self, alias: str) -> bool:
        return alias in self.aliases()

    def _cfg(self, alias: str) -> dict[str, Any]:
        return self.settings.get("models.models", {}).get(alias, {})

    def metered(self, alias: str) -> bool:
        return metered(self._cfg(alias))

    def site_key(self, provider: str) -> bool:
        """Whether the site's own key (or URL) for a hosted provider is in the environment."""
        cfg = self.settings.get(f"models.providers.{provider}", {}) or {}
        if provider == "anthropic":
            return bool(os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY")))
        if provider == "openai_compatible":
            return bool(cfg.get("base_url") or os.environ.get(cfg.get("base_url_env", "")))
        return True

    def reason(self, alias: str, *, byo: bool = False, budget_ok: bool | None = None) -> str | None:
        """Why the alias cannot answer a turn now, or None when it can."""
        provider = self._cfg(alias).get("provider", "")
        if provider not in self.h.providers:
            return PROVIDER_UNCONFIGURED
        if provider in HOSTED_PROVIDERS and not byo and not self.site_key(provider):
            return PROVIDER_UNCONFIGURED
        if not self.h.provider_health.ok(provider):
            return PROVIDER_UNHEALTHY
        if self.metered(alias) and not byo:
            ok = self.h.hosted_budget.ok() if budget_ok is None else budget_ok
            if not ok:
                return BUDGET_EXHAUSTED
        return None

    def alias_ok(
        self, *, byo: bool, budget: bool = True, spent: bool = False
    ) -> Callable[[str], bool]:
        """The router's alias filter for one public call. `budget=False` ignores the budget
        (to tell whether the budget is what moved a route); `spent=True` treats it as spent
        (the ledger refused to hold the call's worst case)."""
        budget_ok = False if spent else (None if budget else True)

        def ok(alias: str) -> bool:
            if not self.allowed(alias):
                return False
            return self.reason(alias, byo=byo, budget_ok=budget_ok) is None

        return ok

    def effective_default(self, byo: bool = False) -> str | None:
        """What a public turn without a hint runs on now: the default model when it can
        answer, else the first alias of `public.models` (default first) that can."""
        ordered = self.ordered()
        return next((a for a in ordered if self.reason(a, byo=byo) is None), None)

    def ordered(self) -> list[str]:
        aliases = self.aliases()
        default = self.settings.get("public.default_model", None)
        if default in aliases:
            aliases.remove(default)
            aliases.insert(0, default)
        return aliases


def resets_at_iso(ts: float) -> str:
    return iso(ts).split(".")[0].rstrip("Z") + "Z"


class StreamRefused(Exception):
    """No stream slot: `scope` says which cap was full (client, tenant, ops or server)."""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class StreamSlots:
    """A bound on SSE streams open at once (`gateway.max_open_streams`, every mode).

    A slot is taken when the route answers and given back when the stream ends. A slot whose
    stream never started (the client went away first) lapses after `grace_s`, so the count can
    never leak.

    P6 review, public mode: one client cannot hold the server's slots. A slot also counts against
    the client's address bucket (`public.max_streams_per_ip`, IPv6 by /64) and its tenant
    (`public.max_streams_per_tenant`), both checked before the server-wide cap, and the tenant
    ops stream (GET /v1/events) draws on a smaller pool (`public.max_ops_streams`) so it can
    never starve the turn streams. A cap of 0 is off (dev and private mode).
    """

    def __init__(
        self,
        cap: int = 50,
        grace_s: float = 30.0,
        *,
        per_ip: int = 0,
        per_tenant: int = 0,
        ops_cap: int = 0,
    ) -> None:
        self.cap = max(1, int(cap))
        self.grace_s = grace_s
        self.per_ip = max(0, int(per_ip))
        self.per_tenant = max(0, int(per_tenant))
        self.ops_cap = max(0, int(ops_cap))
        # token -> [time taken (inf once its stream started), ip bucket, tenant, pool]
        self._open: dict[int, list[Any]] = {}
        self._next = 0

    def acquire(self, ip: str = "", tenant: str = "", pool: str = "turn") -> int:
        """A slot, or StreamRefused naming the cap that was full."""
        now = time.monotonic()
        for token, (taken, *_rest) in list(self._open.items()):
            if now - taken > self.grace_s:
                del self._open[token]
        slots = list(self._open.values())

        def held(i: int, value: str) -> int:
            return sum(1 for slot in slots if slot[i] == value)

        if self.per_ip and ip and held(1, ip) >= self.per_ip:
            raise StreamRefused("client")
        if self.per_tenant and tenant and held(2, tenant) >= self.per_tenant:
            raise StreamRefused("tenant")
        if pool == "ops" and self.ops_cap and held(3, "ops") >= self.ops_cap:
            raise StreamRefused("ops")
        if len(slots) >= self.cap:
            raise StreamRefused("server")
        self._next += 1
        self._open[self._next] = [now, ip, tenant, pool]
        return self._next

    def started(self, token: int) -> None:
        if token in self._open:
            self._open[token][0] = float("inf")

    def release(self, token: int) -> None:
        self._open.pop(token, None)

    def __len__(self) -> int:
        return len(self._open)


def _when(ts: float | None) -> str | None:
    return None if ts is None else iso(ts).split(".")[0] + "Z"


def capabilities_block(h: Any, identity: Any = None, ip: str | None = None) -> dict[str, Any]:
    """`GET /v1/capabilities` `public` (http_api.md, P6 additions), in every mode.

    Public mode: the allowlist with each alias's availability for a site-paid turn, the
    tenant's expiry, the IP's remaining turns this hour and the budget without dollars. Other
    modes: every non-embedding alias with `available` from its provider, an `ok` budget and no
    expiry, so the UI has one code path.
    """
    s = h.settings
    public = s.mode == "public"
    models_cfg = s.get("models.models", {})
    gateway = s.get("gateway", {})
    per_ip = int((gateway.get("rate_limit") or {}).get("per_ip_per_hour", 30))
    size = s.get("input_guardrails.size.max_chars", None)
    max_chars = int(gateway.get("max_message_chars", 32000))
    if (
        s.get("input_guardrails.enabled", True)
        and size
        and "size" in s.get("input_guardrails.checks", ["size"])
    ):
        max_chars = min(max_chars, int(size))
    limits = {
        "per_ip_per_hour": per_ip,
        # Outside public mode no IP limit applies: the full allowance, so the field stays a number.
        "remaining_this_hour": h.gateway.rate_remaining(ip or "") if public else per_ip,
        "max_steps": int(s.get("loop.max_steps", 8)),
        "budget_tokens": s.get("loop.budget_tokens", None),
        "max_message_chars": max_chars,
    }
    pm: PublicModels = h.public_models
    if public:
        aliases = pm.aliases()
        default = pm.effective_default()
        budget = h.hosted_budget.public_view()
        reasons = {a: pm.reason(a) for a in aliases}
    else:
        aliases = [a for a, cfg in models_cfg.items() if not cfg.get("embedding")]
        try:
            default = h.router.route(_default_request(s.mode)).model
        except Exception:
            default = None
        budget = {"status": "ok", "resets_at": None, "spent_fraction": 0}
        reasons = {}
        for alias in aliases:
            provider = models_cfg[alias].get("provider", "")
            if provider not in h.providers:
                reasons[alias] = PROVIDER_UNCONFIGURED
            elif not h.provider_health.ok(provider):
                reasons[alias] = PROVIDER_UNHEALTHY
            else:
                reasons[alias] = None
    models = [
        {
            "alias": alias,
            "label": str(models_cfg.get(alias, {}).get("label") or alias),
            "provider": models_cfg.get(alias, {}).get("provider"),
            "default": alias == default,
            "available": reasons[alias] is None,
            "reason": reasons[alias],
        }
        for alias in aliases
    ]
    expires = getattr(identity, "expires_at", None) if public else None
    return {
        "session_expires_at": _when(expires),
        "ttl_s": int(s.get("public.session_ttl_s", 7200)),
        "limits": limits,
        "models": models,
        "budget": budget,
        "byo_key_bypasses_budget": True,
    }


def _default_request(mode: str) -> Any:
    from harnesswalk.execution.router import RouteRequest

    return RouteRequest(step=1, step_kind="first", mode=mode)
