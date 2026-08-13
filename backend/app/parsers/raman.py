"""Raman ASCII export parsing (generic CSV/TXT, Renishaw WiRE, HORIBA LabSpec)."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from app.models.spectrum import ParseWarning, SpectrumMeta
from app.parsers.tabular import ParseError, TableParse, parse_table

X_PATTERNS = re.compile(
    r"(\bwave\b|wave\s*num|wavenumber|raman\s*shift|shift|cm-?1|cm\^?-?1|abscissa)",
    re.IGNORECASE,
)
Y_PATTERNS = re.compile(
    r"(intensit|counts?|cts|signal|ordinate|#\s*inten|arb)", re.IGNORECASE
)
SPATIAL_PATTERNS = re.compile(r"^(x|y|z|pos|position|row|col)\b", re.IGNORECASE)

# Raman shifts outside this window are not physically useful for graphene.
PLAUSIBLE_MIN, PLAUSIBLE_MAX = -200.0, 5000.0
# The analysis needs at least the G region; warn loudly if it is missing.
REQUIRED_MIN, REQUIRED_MAX = 1200.0, 1700.0

_LASER_LINES = (325.0, 442.0, 457.0, 473.0, 488.0, 514.5, 532.0, 633.0, 660.0, 785.0, 830.0, 1064.0)


def _detect_excitation(header: str) -> tuple[float | None, str | None]:
    """Pull the laser wavelength out of vendor header text."""
    if not header:
        return None, None
    patterns = [
        r"(?:laser|excitation|wavelength|lambda)[^\d\n]{0,20}(\d{3,4}(?:\.\d+)?)\s*nm",
        r"(\d{3,4}(?:\.\d+)?)\s*nm",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, header, re.IGNORECASE):
            value = float(match.group(1))
            if 200.0 <= value <= 1200.0:
                # Snap to the nearest standard line when within 2 nm.
                nearest = min(_LASER_LINES, key=lambda line: abs(line - value))
                if abs(nearest - value) <= 2.0:
                    return nearest, match.group(0).strip()
                return value, match.group(0).strip()
    return None, None


def _detect_instrument(header: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    lowered = header.lower()
    if "wire" in lowered or "renishaw" in lowered:
        hints["vendor"] = "Renishaw"
    elif "labspec" in lowered or "horiba" in lowered or "jobin" in lowered:
        hints["vendor"] = "HORIBA"
    elif "witec" in lowered:
        hints["vendor"] = "WITec"
    elif "thermo" in lowered or "omnic" in lowered:
        hints["vendor"] = "Thermo"
    for key in ("Acq. time", "Accumulations", "Objective", "Grating", "Hole", "Slit"):
        match = re.search(rf"{re.escape(key)}\s*[=:]\s*([^\n#]+)", header, re.IGNORECASE)
        if match:
            hints[key.lower().replace(". ", "_").replace(" ", "_")] = match.group(1).strip()[:80]
    return hints


def _is_spatial_grid(series: pd.Series) -> bool:
    """Map exports repeat each stage coordinate across a whole spectrum."""
    values = series.to_numpy()
    if len(values) < 20:
        return False
    unique = np.unique(values)
    return len(unique) > 1 and len(unique) <= max(2, len(values) // 10)


def _choose_axes(
    frame: pd.DataFrame, named: bool
) -> tuple[str, str, list[str], dict[str, str]]:
    """Return (x_col, y_col, spatial_cols, detection_detail)."""
    detail: dict[str, str] = {}
    cols = list(frame.columns)

    spatial = [c for c in cols if _is_spatial_grid(frame[c]) and (not named or SPATIAL_PATTERNS.match(str(c)))]
    candidates = [c for c in cols if c not in spatial] or cols

    if named:
        x_named = [c for c in candidates if X_PATTERNS.search(str(c))]
        y_named = [c for c in candidates if Y_PATTERNS.search(str(c))]
        if x_named and y_named and x_named[0] != y_named[0]:
            detail["x"] = f"matched column name {x_named[0]!r}"
            detail["y"] = f"matched column name {y_named[0]!r}"
            return x_named[0], y_named[0], spatial, detail

    # Fall back to physics: the x axis is the monotonic column sitting in a
    # plausible Raman-shift window.
    scored: list[tuple[float, str]] = []
    for col in candidates:
        values = frame[col].to_numpy(dtype=float)
        lo, hi = float(np.min(values)), float(np.max(values))
        if hi <= lo:
            continue
        if lo < PLAUSIBLE_MIN or hi > PLAUSIBLE_MAX:
            continue
        diffs = np.diff(values)
        monotonic = float(np.mean(diffs > 0)) if len(diffs) else 0.0
        monotonic = max(monotonic, 1.0 - monotonic)
        # Reward spanning the D..2D window.
        coverage = min(1.0, (min(hi, 3000.0) - max(lo, 1000.0)) / 2000.0)
        scored.append((monotonic * 2 + max(coverage, 0.0), col))
    if not scored:
        raise ParseError(
            "Could not identify a wavenumber column: no column has monotonic "
            f"values inside {PLAUSIBLE_MIN:g}-{PLAUSIBLE_MAX:g} cm-1."
        )
    scored.sort(reverse=True)
    x_col = scored[0][1]
    detail["x"] = f"inferred from monotonic values spanning the Raman window ({x_col})"

    y_candidates = [c for c in candidates if c != x_col]
    if not y_candidates:
        raise ParseError("Only one usable column found; need wavenumber and intensity.")
    # Intensity has the largest dynamic range relative to its own baseline.
    y_col = max(
        y_candidates,
        key=lambda c: float(np.ptp(frame[c].to_numpy(dtype=float)))
        / (abs(float(np.median(frame[c].to_numpy(dtype=float)))) + 1e-9),
    )
    detail["y"] = f"inferred from largest relative dynamic range ({y_col})"
    return x_col, y_col, spatial, detail


def parse_raman(
    raw: bytes, filename: str, excitation_nm: float | None = None
) -> tuple[np.ndarray, np.ndarray, SpectrumMeta]:
    table: TableParse = parse_table(raw)
    frame = table.frame
    named = table.column_names is not None

    x_col, y_col, spatial, detail = _choose_axes(frame, named)
    warnings = [ParseWarning(code="tabular", message=w) for w in table.warnings]

    x = frame[x_col].to_numpy(dtype=float)
    y = frame[y_col].to_numpy(dtype=float)

    detected_format = "generic_tabular"
    hints = _detect_instrument(table.header_text)
    if hints.get("vendor") == "Renishaw":
        detected_format = "renishaw_wire_ascii"
    elif hints.get("vendor") == "HORIBA":
        detected_format = "horiba_labspec_ascii"

    if spatial:
        # Map/line-scan export: collapse to the mean spectrum per wavenumber.
        detected_format = f"{detected_format}_map"
        grouped = pd.DataFrame({"x": x, "y": y}).groupby("x", as_index=False)["y"].mean()
        n_spectra = len(frame) // max(len(grouped), 1)
        x = grouped["x"].to_numpy(dtype=float)
        y = grouped["y"].to_numpy(dtype=float)
        warnings.append(
            ParseWarning(
                code="map_averaged",
                message=(
                    f"Detected a map/line-scan export with spatial column(s) "
                    f"{', '.join(str(c) for c in spatial)}. Averaged ~{n_spectra} "
                    "spectra into a single mean spectrum."
                ),
            )
        )

    x, y, dedup_note = _clean_axis(x, y)
    if dedup_note:
        warnings.append(ParseWarning(code="duplicates", message=dedup_note))

    if x.size < 10:
        raise ParseError("Fewer than 10 unique wavenumber points after cleaning.")

    x_min, x_max = float(x[0]), float(x[-1])
    if x_min > REQUIRED_MIN or x_max < REQUIRED_MAX:
        warnings.append(
            ParseWarning(
                code="range",
                message=(
                    f"Spectrum spans {x_min:.0f}-{x_max:.0f} cm-1. The D and G bands "
                    f"need {REQUIRED_MIN:.0f}-{REQUIRED_MAX:.0f} cm-1; some metrics "
                    "will be unavailable."
                ),
            )
        )

    header_excitation, matched = _detect_excitation(table.header_text)
    resolved_excitation = excitation_nm or header_excitation
    if excitation_nm and header_excitation and abs(excitation_nm - header_excitation) > 1:
        warnings.append(
            ParseWarning(
                code="excitation_conflict",
                message=(
                    f"File header reports {header_excitation:g} nm but "
                    f"{excitation_nm:g} nm was supplied; using the supplied value."
                ),
            )
        )
    if resolved_excitation is None:
        warnings.append(
            ParseWarning(
                code="excitation_default",
                message=(
                    "No laser wavelength found in the file; assuming 532 nm. "
                    "Crystallite size and defect density scale as lambda^4, so set "
                    "this explicitly if your laser differs."
                ),
            )
        )
    elif matched:
        hints["excitation_match"] = matched

    meta = SpectrumMeta(
        filename=filename,
        technique="raman",
        x_label="Raman shift",
        y_label="Intensity",
        x_unit="cm-1",
        n_points=int(x.size),
        x_min=x_min,
        x_max=x_max,
        delimiter=table.delimiter_name,
        header_lines=table.header_lines,
        detected_format=detected_format,
        columns_detected={"x": str(x_col), "y": str(y_col), **detail},
        instrument_hints=hints,
        excitation_nm=resolved_excitation,
        warnings=warnings,
    )
    return x, y, meta


def _clean_axis(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, str | None]:
    """Sort ascending, drop non-finite points, average duplicate x values."""
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    unique_x, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    if len(unique_x) == len(x):
        return x, y, None
    summed = np.zeros(len(unique_x), dtype=float)
    np.add.at(summed, inverse, y)
    note = (
        f"Averaged {len(x) - len(unique_x)} duplicate x value(s) "
        "(common in map exports and stitched spectra)."
    )
    return unique_x, summed / counts, note
