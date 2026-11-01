"""Structured logs (P6): one JSON object per line on stdout when `logging.format: json`
(env `HARNESSWALK_LOG_FORMAT=json`).

Not a node. A request line has `ts`, `level`, `logger`, `msg: request`, `request_id`, a
`tenant` hash (never the tenant id itself), `method`, `route` (the route template, never the
concrete path or the query string), `status` and `duration_ms`. Other log records (uvicorn,
the harness's warnings) become `{ts, level, logger, msg}`. Nothing here ever logs a message's
content, a header or a key: the request line is built from named fields only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import uuid
from typing import Any

from harnesswalk.events import iso

REQUEST_LOGGER = "harnesswalk.http"
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": iso(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            out.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            out["error"] = record.exc_info[0].__name__  # the type only: messages can hold data
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def configure_json(stream: Any = None) -> None:
    """Route the harness's and uvicorn's loggers to one JSON handler on stdout (once)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    for name in ("harnesswalk", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
    # The request line replaces uvicorn's access log, which prints the path and query.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
    _configured = True


def tenant_hash(tenant_id: str | None) -> str | None:
    if not tenant_id:
        return None
    return hashlib.sha256(tenant_id.encode()).hexdigest()[:12]


def request_id(given: str | None) -> str:
    """The caller's `X-Request-Id` when it is a plain token, else a new one."""
    if given and REQUEST_ID.match(given):
        return given
    return uuid.uuid4().hex[:16]


def request_line(
    *,
    request_id: str,
    method: str,
    route: str,
    status: int,
    duration_ms: float,
    tenant: str | None,
) -> None:
    logging.getLogger(REQUEST_LOGGER).info(
        "request",
        extra={
            "fields": {
                "request_id": request_id,
                "tenant": tenant_hash(tenant),
                "method": method,
                "route": route,
                "status": status,
                "duration_ms": round(duration_ms, 2),
            }
        },
    )
