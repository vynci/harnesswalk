"""HTTP plumbing shared by the hosted and local adapters: one POST, errors mapped to codes."""

from __future__ import annotations

import json
from typing import Any

import httpx

from harnesswalk.execution.providers import ProviderError


async def post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = 60.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            response = await client.post(url, json=body, headers=headers or {})
    except httpx.TimeoutException as exc:
        raise ProviderError("provider_timeout", f"{url} timed out after {timeout_s}s") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("provider_error", f"{url}: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        # The body may echo the request; keep only a short, key-free prefix.
        raise ProviderError("provider_error", f"{url} returned HTTP {response.status_code}")
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_error", f"{url} returned invalid JSON") from exc
