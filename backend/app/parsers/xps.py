"""XPS parsing: generic CSV/TXT exports plus ISO 14976 (VAMAS)."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from app.models.spectrum import ParseWarning, SpectrumMeta, XPSRegion
from app.parsers.tabular import ParseError, parse_table
from app.parsers.vamas import VamasBlock, is_vamas, parse_vamas

BE_PATTERNS = re.compile(
    r"(binding\s*energy|^be\b|b\.?e\.?\s*\(?ev|energy\s*\(ev\)|abscissa)", re.IGNORECASE
)
KE_PATTERNS = re.compile(r"(kinetic\s*energy|^ke\b|k\.?e\.?\s*\(?ev)", re.IGNORECASE)
Y_PATTERNS = re.compile(r"(counts?|cps|intensit|cts|signal|ordinate|arb)", re.IGNORECASE)

# Region windows in binding energy (eV).
C1S_WINDOW = (278.0, 298.0)
O1S_WINDOW = (524.0, 546.0)

# Common anode energies for kinetic->binding conversion.
ANODES = {"al": 1486.6, "mg": 1253.6}


def classify_region(x_min: float, x_max: float) -> XPSRegion:
    span = x_max - x_min
    if span > 100:
        return "survey"
    if x_min >= C1S_WINDOW[0] - 6 and x_max <= C1S_WINDOW[1] + 6:
        return "c1s"
    if x_min >= O1S_WINDOW[0] - 6 and x_max <= O1S_WINDOW[1] + 6:
        return "o1s"
    return "unknown"


def _region_from_labels(species: str, transition: str) -> XPSRegion:
    tag = f"{species} {transition}".lower().replace(" ", "")
    if tag.startswith("c1s") or "c1s" in tag:
        return "c1s"
    if tag.startswith("o1s") or "o1s" in tag:
        return "o1s"
    if "survey" in tag or "wide" in tag:
        return "survey"
    return "unknown"


def _detect_anode(header: str) -> tuple[float | None, str | None]:
    lowered = header.lower()
    match = re.search(r"(?:photon|source|excitation|hv|anode)[^\d\n]{0,20}(\d{3,4}(?:\.\d+)?)", lowered)
    if match:
        value = float(match.group(1))
        if 100.0 <= value <= 10000.0:
            return value, f"header value {value:g} eV"
    for key, energy in ANODES.items():
        if re.search(rf"\b{key}\s*k?(alpha|a)\b", lowered):
            return energy, f"{key.title()} K-alpha"
    return None, None


def parse_xps(
    raw: bytes, filename: str, region_hint: XPSRegion | None = None
) -> list[tuple[np.ndarray, np.ndarray, SpectrumMeta]]:
    """Parse an XPS file into one or more (x, y, meta) regions.

    A VAMAS file usually contains several blocks (survey + high-res regions),
    so this returns a list. Tabular files return a single entry.
    """
    if is_vamas(raw, filename):
        return _parse_vamas_regions(raw, filename)
    return [_parse_tabular_region(raw, filename, region_hint)]


def _parse_vamas_regions(
    raw: bytes, filename: str
) -> list[tuple[np.ndarray, np.ndarray, SpectrumMeta]]:
    blocks = parse_vamas(raw)
    results: list[tuple[np.ndarray, np.ndarray, SpectrumMeta]] = []
    for block in blocks:
        x, y, meta = _finalise_vamas_block(block, filename)
        results.append((x, y, meta))
    if not results:
        raise ParseError("VAMAS file produced no usable regions.")
    return results


def _finalise_vamas_block(
    block: VamasBlock, filename: str
) -> tuple[np.ndarray, np.ndarray, SpectrumMeta]:
    warnings = [ParseWarning(code="vamas", message=w) for w in block.warnings]
    x, y = block.x.astype(float), block.y.astype(float)

    units = block.abscissa_units.strip().lower()
    label = block.abscissa_label.strip().lower()
    is_kinetic = "kinetic" in label or (
        "binding" not in label and block.analysis_source_energy and float(np.max(x)) > 1200
    )
    if is_kinetic and block.analysis_source_energy:
        x = float(block.analysis_source_energy) - x
        warnings.append(
            ParseWarning(
                code="ke_to_be",
                message=(
                    "Converted kinetic energy to binding energy using the source "
                    f"energy {block.analysis_source_energy:g} eV recorded in the file "
                    "(analyser work function not applied)."
                ),
            )
        )
    if units and units not in {"ev", "e v", "electron volt", "electronvolt"}:
        warnings.append(
            ParseWarning(
                code="units",
                message=f"Abscissa units reported as {block.abscissa_units!r}; assuming eV.",
            )
        )

    # Counts per second is fine for ratios, but normalise scans out so blocks
    # with different accumulation counts stay comparable within a file.
    if block.n_scans and block.n_scans > 1:
        y = y / float(block.n_scans)
        warnings.append(
            ParseWarning(
                code="scan_normalised",
                message=f"Divided intensities by {block.n_scans} accumulated scans.",
            )
        )

    x, y = _sort_by_be(x, y)
    region = _region_from_labels(block.species, block.transition)
    if region == "unknown":
        region = classify_region(float(x[0]), float(x[-1]))

    meta = SpectrumMeta(
        filename=filename,
        technique="xps",
        region=region,
        x_label="Binding energy",
        y_label="Intensity",
        x_unit="eV",
        n_points=int(x.size),
        x_min=float(x[0]),
        x_max=float(x[-1]),
        detected_format="vamas_iso14976",
        columns_detected={
            "block": block.block_id,
            "species": block.species,
            "transition": block.transition,
        },
        instrument_hints={
            "sample_id": block.sample_id,
            "technique": block.technique,
            "scan_mode": block.scan_mode,
            **({"source_energy_ev": f"{block.analysis_source_energy:g}"} if block.analysis_source_energy else {}),
        },
        warnings=warnings,
    )
    return x, y, meta


def _parse_tabular_region(
    raw: bytes, filename: str, region_hint: XPSRegion | None
) -> tuple[np.ndarray, np.ndarray, SpectrumMeta]:
    table = parse_table(raw)
    frame = table.frame
    named = table.column_names is not None
    warnings = [ParseWarning(code="tabular", message=w) for w in table.warnings]
    detail: dict[str, str] = {}

    x_col = y_col = None
    axis_is_kinetic = False
    if named:
        be_cols = [c for c in frame.columns if BE_PATTERNS.search(str(c))]
        ke_cols = [c for c in frame.columns if KE_PATTERNS.search(str(c))]
        y_cols = [c for c in frame.columns if Y_PATTERNS.search(str(c))]
        if be_cols and y_cols and be_cols[0] != y_cols[0]:
            x_col, y_col = be_cols[0], y_cols[0]
            detail["x"] = f"matched binding-energy column name {x_col!r}"
        elif ke_cols and y_cols and ke_cols[0] != y_cols[0]:
            x_col, y_col, axis_is_kinetic = ke_cols[0], y_cols[0], True
            detail["x"] = f"matched kinetic-energy column name {x_col!r}"
        if y_col is not None:
            detail["y"] = f"matched column name {y_col!r}"

    if x_col is None:
        x_col, y_col = _infer_energy_axis(frame)
        detail["x"] = f"inferred monotonic energy axis ({x_col})"
        detail["y"] = f"inferred intensity column ({y_col})"

    x = frame[x_col].to_numpy(dtype=float)
    y = frame[y_col].to_numpy(dtype=float)

    anode_energy, anode_note = _detect_anode(table.header_text)
    if axis_is_kinetic:
        if anode_energy is None:
            anode_energy = ANODES["al"]
            warnings.append(
                ParseWarning(
                    code="anode_assumed",
                    message=(
                        "Axis is kinetic energy but no photon energy was found in the "
                        "header; assumed Al K-alpha (1486.6 eV)."
                    ),
                )
            )
        x = anode_energy - x
        detail["conversion"] = f"BE = {anode_energy:g} - KE ({anode_note or 'assumed Al Ka'})"

    finite = np.isfinite(x) & np.isfinite(y)
    x, y = _sort_by_be(x[finite], y[finite])
    if x.size < 10:
        raise ParseError("Fewer than 10 usable data points after cleaning.")

    region = region_hint or classify_region(float(x[0]), float(x[-1]))
    if region == "unknown":
        warnings.append(
            ParseWarning(
                code="region_unknown",
                message=(
                    f"Energy range {x[0]:.1f}-{x[-1]:.1f} eV does not match a survey, "
                    "C 1s or O 1s window. Set the region explicitly on upload."
                ),
            )
        )

    meta = SpectrumMeta(
        filename=filename,
        technique="xps",
        region=region,
        x_label="Binding energy",
        y_label="Intensity",
        x_unit="eV",
        n_points=int(x.size),
        x_min=float(x[0]),
        x_max=float(x[-1]),
        delimiter=table.delimiter_name,
        header_lines=table.header_lines,
        detected_format="generic_tabular",
        columns_detected={"x": str(x_col), "y": str(y_col), **detail},
        instrument_hints={"anode": anode_note} if anode_note else {},
        warnings=warnings,
    )
    return x, y, meta


def _infer_energy_axis(frame: pd.DataFrame) -> tuple[str, str]:
    best: tuple[float, str] | None = None
    for col in frame.columns:
        values = frame[col].to_numpy(dtype=float)
        lo, hi = float(np.min(values)), float(np.max(values))
        if hi <= lo or lo < -50 or hi > 2000:
            continue
        diffs = np.diff(values)
        monotonic = float(np.mean(diffs > 0)) if len(diffs) else 0.0
        monotonic = max(monotonic, 1.0 - monotonic)
        if best is None or monotonic > best[0]:
            best = (monotonic, str(col))
    if best is None:
        raise ParseError(
            "Could not identify an energy axis: no column is monotonic within "
            "0-2000 eV."
        )
    x_col = best[1]
    others = [c for c in frame.columns if str(c) != x_col]
    if not others:
        raise ParseError("Only one usable column found; need energy and intensity.")
    y_col = max(others, key=lambda c: float(np.ptp(frame[c].to_numpy(dtype=float))))
    return x_col, str(y_col)


def _sort_by_be(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return ascending binding energy with duplicates averaged."""
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    unique_x, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    if len(unique_x) == len(x):
        return x, y
    summed = np.zeros(len(unique_x), dtype=float)
    np.add.at(summed, inverse, y)
    return unique_x, summed / counts
