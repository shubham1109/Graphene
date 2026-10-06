"""Parse the way technical data sheets quote numbers.

Datasheets rarely give a single number: "20-50", "<10", ">99.5", "~300",
"130-180", "≤ 1", "0.02-0.2", "1e5". This module turns any of those into a
closed or half-open interval so the same code handles the vendor database,
the literature tables and whatever the user types into the finder form.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_RANGE_SEP = r"\s*(?:-|–|—|~|to|∼|…|\.\.)\s*"
_CLEAN = re.compile(r"[, ]")


@dataclass(frozen=True)
class SpecValue:
    """An interval with the original text kept for display."""

    low: float | None
    high: float | None
    text: str

    @property
    def is_point(self) -> bool:
        return self.low is not None and self.low == self.high

    def nominal(self, log_scale: bool = False) -> float | None:
        """A single representative number for distance calculations."""
        if self.low is not None and self.high is not None:
            if log_scale and self.low > 0 and self.high > 0:
                return math.sqrt(self.low * self.high)
            return (self.low + self.high) / 2.0
        bound = self.low if self.low is not None else self.high
        return bound

    def to_dict(self) -> dict:
        return {"low": self.low, "high": self.high, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict | None) -> "SpecValue | None":
        if not data:
            return None
        return cls(data.get("low"), data.get("high"), data.get("text", ""))

    @classmethod
    def point(cls, value: float, text: str | None = None) -> "SpecValue":
        return cls(float(value), float(value), text if text is not None else f"{value:g}")


def _to_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def parse_spec_value(raw: object, scale: float = 1.0) -> SpecValue | None:
    """Turn a datasheet cell into an interval; None when it holds no number.

    `scale` multiplies every bound (unit conversion, e.g. nm -> um is 1e-3).
    Comparators: "<10" -> (None, 10); ">99" -> (99, None); "≤"/"≥" likewise;
    "~300" and "≈300" are points. A leading zero in "01-0.8" is tolerated.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and math.isnan(raw):
            return None
        value = float(raw) * scale
        return SpecValue(value, value, f"{raw:g}")

    text = str(raw).strip()
    if not text or text in {"-", "–", "—", "n/a", "N/A", "na", "NA", "nan", "None", "?"}:
        return None

    cleaned = _CLEAN.sub("", text)
    cleaned = cleaned.replace("≤", "<=").replace("≥", ">=").replace("＜", "<").replace("＞", ">")
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Comparator forms. Tolerate "> 99 %", "<=10um", "Min. 50".
    comparator = re.match(rf"^(<=|>=|<|>|min\.?|max\.?)\s*({_NUMBER})", cleaned, flags=re.I)
    if comparator:
        op, number = comparator.group(1).lower(), _to_float(comparator.group(2))
        if number is None:
            return None
        number *= scale
        if op in {"<", "<=", "max", "max."}:
            return SpecValue(None, number, text)
        return SpecValue(number, None, text)

    # Approximate forms are treated as points.
    approx = re.match(rf"^(?:~|≈|approx\.?|about|ca\.?)\s*({_NUMBER})", cleaned, flags=re.I)
    if approx:
        number = _to_float(approx.group(1))
        if number is None:
            return None
        return SpecValue(number * scale, number * scale, text)

    # Ranges: "20-50", "0.02 - 0.2", "130 to 180", "400 ~1000", "3±0.5".
    plus_minus = re.match(rf"^({_NUMBER})\s*(?:±|\+/-|\+-)\s*({_NUMBER})", cleaned)
    if plus_minus:
        centre, spread = _to_float(plus_minus.group(1)), _to_float(plus_minus.group(2))
        if centre is None or spread is None:
            return None
        return SpecValue((centre - spread) * scale, (centre + spread) * scale, text)

    rng = re.match(rf"^({_NUMBER}){_RANGE_SEP}({_NUMBER})", cleaned)
    if rng:
        a, b = _to_float(rng.group(1)), _to_float(rng.group(2))
        if a is None or b is None:
            return None
        low, high = sorted((a, b))
        return SpecValue(low * scale, high * scale, text)

    single = re.match(rf"^({_NUMBER})", cleaned)
    if single:
        number = _to_float(single.group(1))
        if number is None:
            return None
        return SpecValue(number * scale, number * scale, text)

    # A number buried in text, e.g. "C 99.8%", "O <7" or "Fe 0.02". Take the
    # first one, honouring a comparator or range attached to it.
    buried = re.search(
        rf"(<=|>=|<|>)?\s*({_NUMBER})(?:{_RANGE_SEP}({_NUMBER}))?", cleaned
    )
    if buried:
        op, first, second = buried.group(1), _to_float(buried.group(2)), buried.group(3)
        if first is None:
            return None
        if second is not None and _to_float(second) is not None and op is None:
            low, high = sorted((first, _to_float(second)))
            return SpecValue(low * scale, high * scale, text)
        if op in {"<", "<="}:
            return SpecValue(None, first * scale, text)
        if op in {">", ">="}:
            return SpecValue(first * scale, None, text)
        return SpecValue(first * scale, first * scale, text)
    return None


def describe(value: SpecValue | None, unit: str = "", digits: int = 3) -> str:
    """Human-readable interval, e.g. '20 - 50 um', '< 10 um', '> 99 %'."""
    if value is None:
        return "not specified"
    suffix = f" {unit}" if unit else ""

    def fmt(x: float) -> str:
        if x != 0 and (abs(x) >= 1e6 or abs(x) < 1e-3):
            return f"{x:.{digits}g}"
        rounded = float(f"{x:.{digits}g}")
        return f"{rounded:,.{digits}f}".rstrip("0").rstrip(".") if rounded != int(rounded) else f"{int(rounded):,}"

    if value.low is not None and value.high is not None:
        if value.low == value.high:
            return f"{fmt(value.low)}{suffix}"
        return f"{fmt(value.low)} - {fmt(value.high)}{suffix}"
    if value.low is not None:
        return f"> {fmt(value.low)}{suffix}"
    if value.high is not None:
        return f"< {fmt(value.high)}{suffix}"
    return "not specified"
