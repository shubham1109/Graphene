"""Property / DFT spec-sheet parsing (JSON object or two-column CSV)."""

from __future__ import annotations

import json
import re

from app.models.sample import SampleProperties
from app.parsers.tabular import ParseError, decode_bytes

# Canonical property -> accepted aliases (matched case/punctuation-insensitively).
ALIASES: dict[str, tuple[str, ...]] = {
    "flake_size_um": ("flake size", "lateral size", "lateral dimension", "particle size", "d50", "flake diameter"),
    "bet_m2_g": ("bet", "bet surface area", "specific surface area", "ssa", "surface area"),
    "thickness_nm": ("thickness", "flake thickness", "average thickness"),
    "bulk_density_g_cm3": ("bulk density", "tap density", "density"),
    "carbon_purity_pct": ("carbon purity", "carbon content", "purity", "c content"),
    "band_gap_ev": ("band gap", "bandgap", "gap", "eg", "optb88vdw bandgap", "hse gap"),
    "youngs_modulus_gpa": ("youngs modulus", "young modulus", "elastic modulus", "e modulus", "stiffness"),
    "carrier_mobility_cm2_vs": ("carrier mobility", "mobility", "electron mobility", "hole mobility"),
    "sheet_resistance_ohm_sq": ("sheet resistance", "rs", "sheet res", "surface resistivity"),
}

# Unit conversions into the canonical unit for each property.
UNIT_FACTORS: dict[str, dict[str, float]] = {
    "flake_size_um": {"um": 1.0, "µm": 1.0, "micron": 1.0, "microns": 1.0, "nm": 1e-3, "mm": 1e3},
    "bet_m2_g": {"m2/g": 1.0, "m^2/g": 1.0, "m²/g": 1.0},
    "thickness_nm": {"nm": 1.0, "um": 1e3, "µm": 1e3, "a": 0.1, "angstrom": 0.1, "å": 0.1},
    "bulk_density_g_cm3": {"g/cm3": 1.0, "g/cm^3": 1.0, "g/cc": 1.0, "kg/m3": 1e-3},
    "carbon_purity_pct": {"%": 1.0, "pct": 1.0, "wt%": 1.0},
    "band_gap_ev": {"ev": 1.0, "mev": 1e-3},
    "youngs_modulus_gpa": {"gpa": 1.0, "mpa": 1e-3, "tpa": 1e3, "pa": 1e-9},
    "carrier_mobility_cm2_vs": {"cm2/vs": 1.0, "cm^2/vs": 1.0, "cm2v-1s-1": 1.0, "m2/vs": 1e4},
    "sheet_resistance_ohm_sq": {"ohm/sq": 1.0, "ohm/sqr": 1.0, "ohms/sq": 1.0, "ω/sq": 1.0, "kohm/sq": 1e3},
}

_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(key).lower()).strip()


def _match_property(key: str) -> str | None:
    norm = _normalise_key(key)
    if not norm:
        return None
    compact = norm.replace(" ", "_")
    if compact in ALIASES:
        return compact
    for canonical, aliases in ALIASES.items():
        if norm == _normalise_key(canonical):
            return canonical
        for alias in aliases:
            if norm == _normalise_key(alias):
                return canonical
    # Fall back to substring containment, longest alias first so "carbon
    # purity" is not shadowed by "purity".
    scored: list[tuple[int, str]] = []
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            alias_norm = _normalise_key(alias)
            if alias_norm and alias_norm in norm:
                scored.append((len(alias_norm), canonical))
    if scored:
        scored.sort(reverse=True)
        return scored[0][1]
    return None


def _coerce_value(prop: str, raw, key_text: str) -> tuple[float | None, str | None]:
    """Return (value_in_canonical_units, warning)."""
    unit_text = ""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    elif isinstance(raw, str):
        match = _NUM.search(raw)
        if not match:
            return None, f"Could not read a number from {key_text!r} = {raw!r}."
        value = float(match.group(0))
        unit_text = raw[match.end():].strip()
    elif isinstance(raw, dict):
        inner = raw.get("value", raw.get("val"))
        if inner is None:
            return None, f"Object for {key_text!r} has no 'value' field."
        value, warn = _coerce_value(prop, inner, key_text)
        unit_text = str(raw.get("unit", raw.get("units", "")))
        if value is None:
            return None, warn
    else:
        return None, f"Unsupported value type for {key_text!r}: {type(raw).__name__}."

    # A unit in the key itself ("BET (m2/g)") is as good as one in the value.
    if not unit_text:
        bracket = re.search(r"[\(\[]([^)\]]+)[)\]]", key_text)
        if bracket:
            unit_text = bracket.group(1)

    warning = None
    if unit_text:
        unit_key = re.sub(r"[\s·*]+", "", unit_text.lower()).replace("^", "")
        factors = UNIT_FACTORS.get(prop, {})
        normalised = {re.sub(r"[\s·*]+", "", k.lower()).replace("^", ""): v for k, v in factors.items()}
        if unit_key in normalised:
            factor = normalised[unit_key]
            if factor != 1.0:
                value *= factor
        elif unit_key not in {"", "-"}:
            warning = (
                f"Unrecognised unit {unit_text!r} for {prop}; value taken as-is in "
                "the canonical unit."
            )
    return value, warning


def parse_spec(raw: bytes, filename: str) -> tuple[SampleProperties, dict, list[str]]:
    """Parse a spec sheet into SampleProperties.

    Returns (properties, raw_key_value_map, warnings).
    """
    text, _ = decode_bytes(raw)
    stripped = text.lstrip()

    if filename.lower().endswith(".json") or stripped.startswith(("{", "[")):
        pairs, warnings = _flatten_json(text)
    else:
        pairs, warnings = _read_key_value_csv(text)

    if not pairs:
        raise ParseError("No key/value pairs found in spec sheet.")

    resolved: dict[str, float] = {}
    matched_keys: dict[str, str] = {}
    for key, value in pairs.items():
        prop = _match_property(key)
        if prop is None:
            continue
        coerced, warn = _coerce_value(prop, value, key)
        if warn:
            warnings.append(warn)
        if coerced is None:
            continue
        if prop in resolved:
            warnings.append(
                f"Duplicate value for {prop}: kept {matched_keys[prop]!r}, ignored {key!r}."
            )
            continue
        resolved[prop] = coerced
        matched_keys[prop] = key

    if not resolved:
        warnings.append(
            "No recognised properties found. Supported keys include: "
            + ", ".join(sorted(ALIASES))
        )

    try:
        properties = SampleProperties(**resolved)
    except Exception as exc:
        raise ParseError(f"Spec values failed validation: {exc}") from exc
    return properties, pairs, warnings


def _flatten_json(text: str) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON: {exc.msg} at line {exc.lineno}.") from exc

    if isinstance(payload, list):
        if not payload:
            raise ParseError("JSON array is empty.")
        if len(payload) > 1:
            warnings.append(
                f"Spec file contains {len(payload)} records; using the first one."
            )
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ParseError("Expected a JSON object of property names to values.")

    flat: dict = {}

    def walk(node: dict, prefix: str = "") -> None:
        for key, value in node.items():
            path = f"{prefix} {key}".strip()
            if isinstance(value, dict) and not {"value", "val"} & value.keys():
                walk(value, path)
            else:
                flat[path] = value

    walk(payload)
    return flat, warnings


def _read_key_value_csv(text: str) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    flat: dict = {}
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and not ln.startswith(("#", "%", ";"))]
    if not lines:
        raise ParseError("Spec sheet is empty.")

    delimiter = max([",", "\t", ";", ":"], key=lambda d: sum(d in ln for ln in lines))
    rows = [[cell.strip().strip('"') for cell in ln.split(delimiter)] for ln in lines]

    # Wide layout: one header row of property names, one row of values.
    if len(rows) == 2 and len(rows[0]) > 2 and len(rows[0]) == len(rows[1]):
        return dict(zip(rows[0], rows[1])), warnings

    for row in rows:
        if len(row) < 2:
            continue
        key, value = row[0], row[1]
        # "BET, 250, m2/g" -> fold the unit back onto the value.
        if len(row) > 2 and row[2] and not _NUM.fullmatch(row[2]):
            value = f"{value} {row[2]}"
        if key in flat:
            warnings.append(f"Duplicate row for {key!r}; kept the first value.")
            continue
        flat[key] = value
    return flat, warnings
