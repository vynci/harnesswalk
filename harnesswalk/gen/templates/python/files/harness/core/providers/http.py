"""A JSON POST over the standard library, shared by the HTTP providers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from harness.core.events import NodeError


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(url, json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured URL
            return json.loads(resp.read())
    except TimeoutError as exc:
        raise NodeError("provider_timeout", f"{url} timed out") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NodeError("provider_error", f"{url}: {str(exc)[:200]}") from exc
