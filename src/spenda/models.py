from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "TokenUsage | None":
        if not isinstance(value, dict):
            return None
        numbers: dict[str, int] = {}
        for field in TOKEN_FIELDS:
            raw = value.get(field, 0)
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or isinstance(raw, float) and not isfinite(raw)
            ):
                raw = 0
            numbers[field] = max(0, int(raw))
        # Across observed Codex versions, occasional UI token_count records had
        # a stale/nonzero total with every billable category set to zero. The
        # stable accounting identity is input + output; reasoning is a subset
        # of output. Normalize instead of trusting that legacy display field.
        numbers["total_tokens"] = numbers["input_tokens"] + numbers["output_tokens"]
        return cls(**numbers)

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens - self.cache_write_input_tokens)

    def fingerprint(self) -> tuple[int, ...]:
        return tuple(getattr(self, field) for field in TOKEN_FIELDS)

    def delta_from(self, previous: "TokenUsage") -> tuple["TokenUsage", bool]:
        reset = any(getattr(self, f) < getattr(previous, f) for f in TOKEN_FIELDS)
        if reset:
            return self, True
        values = {f: getattr(self, f) - getattr(previous, f) for f in TOKEN_FIELDS}
        return TokenUsage(**values), False


@dataclass(slots=True)
class CostResult:
    price_id: int | None
    uncached_input_usd: Decimal | None
    cached_input_usd: Decimal | None
    cache_write_usd: Decimal | None
    output_usd: Decimal | None
    total_usd: Decimal | None
    note: str | None = None
