"""HTTP plumbing shared by the hosted and local adapters: one POST, errors mapped to codes."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from harnesswalk.execution.providers import ProviderError

ERROR_CHARS = 200
SECRET_HEADERS = {"x-api-key", "authorization", "api-key"}
_KEYLIKE = re.compile(r"\b(?:sk|xg[a-z]?)[-_][A-Za-z0-9_-]{6,}")


def error_message(response: httpx.Response, headers: dict[str, str]) -> str:
    """The provider's own reason for an error status, short and key-free, or "".

    Anthropic and OpenAI-compatible servers answer `{"error": {"message": ...}}`, Ollama
    `{"error": "..."}`. The body may echo the request, so the key headers' values and anything
    shaped like a key are masked, and the text is capped."""
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    if not isinstance(error, str) or not error.strip():
        return ""
    text = " ".join(error.split())
    for name, value in headers.items():
        if name.lower() in SECRET_HEADERS:
            for secret in (value, value.rsplit(" ", 1)[-1]):  # "Bearer <key>" too
                if len(secret) >= 4:
                    text = text.replace(secret, "[redacted]")
    text = _KEYLIKE.sub("[redacted]", text)
    return text[:ERROR_CHARS] + ("..." if len(text) > ERROR_CHARS else "")


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
        status = response.status_code
        reason = error_message(response, headers or {})
        raise ProviderError(
            "provider_error",
            f"{url} returned HTTP {status}" + (f": {reason}" if reason else ""),
            client_error=400 <= status < 500 and status not in (408, 429),
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("provider_error", f"{url} returned invalid JSON") from exc


async def get_ok(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """A health probe: True when the GET answers 2xx. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            response = await client.get(url, headers=headers or {})
    except httpx.HTTPError:
        return False
    return 200 <= response.status_code < 300
