"""Token counting. P1 uses the configured estimator; a model tokenizer can replace it later."""

from __future__ import annotations

import math


def estimate(text: str, estimator: str = "chars_div_4") -> int:
    """`chars_div_4`: one token per four characters, rounded up. Cheap, and close enough."""
    if not text:
        return 0
    if estimator != "chars_div_4":
        raise ValueError(f"unknown token estimator {estimator!r}")
    return math.ceil(len(text) / 4)
