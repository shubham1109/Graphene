"""Orchestration: raw spectra in, full analysis report out."""

from __future__ import annotations

import numpy as np

from app.analysis.applications import recommend_applications
from app.analysis.classify import classify
from app.analysis.peers import match_peers
from app.analysis.raman import analyse_raman
from app.analysis.xps import analyse_xps
from app.models.analysis import (
    DFTResult,
    GlobalRanking,
    PropertyComparison,
    RamanResult,
    ScorecardRow,
    Verdict,
    XPSResult,
)
from app.models.sample import SampleProperties

ENGINE_VERSION = "1.0.0"

# What "good" looks like for each classified form. These are quality targets,
# not application requirements - a GO sample is supposed to have a low C/O.
FORM_TARGETS: dict[str, dict[str, tuple[float | None, float | None, str]]] = {
    "monolayer": {
        "sp2_fraction": (0.85, None, "Near-complete sp2 network"),
        "crystallite_size_la_nm": (100.0, None, "Large defect-free domains"),
        "id_ig": (None, 0.1, "Electronic-grade monolayer should be near defect-free"),
        "i2d_ig": (1.6, None, "Monolayer signature"),
        "fwhm_2d_cm1": (None, 45.0, "Narrow 2D confirms single layer"),
        "co_ratio": (20.0, None, "Low residual oxygen after transfer"),
    },
    "bilayer": {
        "sp2_fraction": (0.85, None, "Near-complete sp2 network"),
        "id_ig": (None, 0.2, "Low defect density expected"),
        "fwhm_2d_cm1": (40.0, 60.0, "Bilayer 2D envelope"),
        "co_ratio": (20.0, None, "Low residual oxygen"),
    },
    "few_layer": {
        "sp2_fraction": (0.8, None, "Largely intact sp2 network"),
        "crystallite_size_la_nm": (20.0, None, "Domains large enough to carry load and current"),
        "id_ig": (None, 0.5, "Few-layer graphene should show limited basal-plane damage"),
        "fwhm_2d_cm1": (50.0, 78.0, "Few-layer 2D envelope"),
        "co_ratio": (15.0, None, "Minimal oxidation"),
        "bet_m2_g": (100.0, 700.0, "Consistent with exfoliated few-layer material"),
    },
    "multilayer_gnp": {
        "sp2_fraction": (0.75, None, "Graphitic character retained"),
        "crystallite_size_la_nm": (15.0, None, "Domains large enough for reinforcement"),
        "id_ig": (None, 0.6, "Platelets should retain graphitic order"),
        "co_ratio": (15.0, None, "Minimal oxidation"),
        "bet_m2_g": (20.0, 500.0, "Typical GNP range"),
        "flake_size_um": (1.0, None, "Aspect ratio drives performance"),
    },
    "graphene_oxide": {
        "sp2_fraction": (None, 0.5, "Oxidation converts most carbon to sp3"),
        "id_ig": (0.7, 1.5, "Extensive sp3 conversion is expected in GO"),
        "co_ratio": (1.5, 4.0, "Fully oxidised GO"),
        "flake_size_um": (0.5, None, "Lateral size drives barrier performance"),
    },
    "reduced_graphene_oxide": {
        "sp2_fraction": (0.5, 0.9, "Partially restored sp2 network"),
        "id_ig": (0.7, 1.5, "Small restored sp2 domains keep the D band strong"),
        "co_ratio": (5.0, 15.0, "Partial reduction"),
        "bet_m2_g": (150.0, 800.0, "High surface area from restacking-resistant sheets"),
    },
    "turbostratic": {
        "sp2_fraction": (0.8, None, "High sp2 content despite turbostratic stacking"),
        "id_ig": (None, 0.4, "Flash graphene should be low-defect"),
        "co_ratio": (18.0, None, "Solvent-free routes leave little oxygen"),
        "fwhm_2d_cm1": (40.0, 80.0, "Symmetric turbostratic 2D band"),
    },
    "amorphous_carbon": {
        "id_ig": (None, 1.4, "Above this the material is not usefully graphenic"),
    },
}

METRIC_META = {
    "id_ig": ("I(D)/I(G)", None, True),
    "i2d_ig": ("I(2D)/I(G)", None, False),
    "fwhm_2d_cm1": ("FWHM(2D)", "cm-1", True),
    "co_ratio": ("C/O ratio", None, False),
    "sp2_fraction": ("sp2 fraction", None, False),
    "bet_m2_g": ("BET surface area", "m2/g", False),
    "flake_size_um": ("Flake size", "um", False),
    "crystallite_size_la_nm": ("Crystallite size L_a", "nm", False),
    "defect_density_cm2": ("Defect density", "cm-2", True),
}

DFT_PROPERTIES = {
    "band_gap_ev": ("Band gap", "eV"),
    "youngs_modulus_gpa": ("Young's modulus", "GPa"),
    "carrier_mobility_cm2_vs": ("Carrier mobility", "cm2/Vs"),
    "sheet_resistance_ohm_sq": ("Sheet resistance", "ohm/sq"),
}


def run_raman(x: np.ndarray, y: np.ndarray, excitation_nm: float | None, spectrum_id: str | None):
    return analyse_raman(x, y, excitation_nm, spectrum_id)


def run_xps(regions: dict, photon_energy: float, spectrum_ids: list[str]):
    return analyse_xps(regions, photon_energy, spectrum_ids)


def collect_all_metrics(
    raman: RamanResult | None, xps: XPSResult | None, properties: SampleProperties | None
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if raman is not None:
        for name in ("id_ig", "i2d_ig", "fwhm_2d_cm1", "crystallite_size_la_nm", "defect_density_cm2"):
            value = getattr(raman, name)
            if value is not None:
                metrics[name] = float(value)
    if xps is not None:
        for name in ("co_ratio", "sp2_fraction"):
            value = getattr(xps, name)
            if value is not None:
                metrics[name] = float(value)
    if properties is not None:
        for name in ("bet_m2_g", "flake_size_um"):
            value = getattr(properties, name, None)
            if value is not None:
                metrics[name] = float(value)
    return metrics


def build_scorecard(form: str, metrics: dict[str, float]) -> list[ScorecardRow]:
    targets = FORM_TARGETS.get(form, {})
    rows: list[ScorecardRow] = []
    for metric, value in metrics.items():
        label, unit, _lower_better = METRIC_META.get(metric, (metric, None, False))
        target = targets.get(metric)
        if target is None:
            rows.append(
                ScorecardRow(
                    metric=metric,
                    label=label,
                    value=round(value, 4),
                    unit=unit,
                    target_range=None,
                    verdict="unknown",
                    comment=f"No quality target defined for {label} in {form.replace('_', ' ')} material.",
                )
            )
            continue
        low, high, reason = target
        verdict, comment = _verdict_for(label, value, low, high, reason, unit)
        rows.append(
            ScorecardRow(
                metric=metric,
                label=label,
                value=round(value, 4),
                unit=unit,
                target_range=_range_text(low, high),
                verdict=verdict,
                comment=comment,
            )
        )
    order = list(METRIC_META)
    rows.sort(key=lambda r: order.index(r.metric) if r.metric in order else 99)
    return rows


def _range_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g} - {high:g}"
    if low is not None:
        return f"> {low:g}"
    if high is not None:
        return f"< {high:g}"
    return "any"


def _verdict_for(
    label: str, value: float, low: float | None, high: float | None, reason: str, unit: str | None
) -> tuple[Verdict, str]:
    suffix = f" {unit}" if unit else ""
    inside = (low is None or value >= low) and (high is None or value <= high)
    if inside:
        return "pass", f"{label} {value:.3g}{suffix} is within target. {reason}."

    if low is not None and value < low:
        margin = (low - value) / abs(low) if low else 1.0
        direction = f"below the {low:g} floor"
    else:
        margin = (value - (high or 0)) / abs(high) if high else 1.0
        direction = f"above the {high:g} ceiling"

    if margin <= 0.25:
        return "borderline", f"{label} {value:.3g}{suffix} is marginally {direction}. {reason}."
    return "fail", f"{label} {value:.3g}{suffix} is {direction}. {reason}."


def build_rankings(
    metrics: dict[str, float], references: list[dict]
) -> list[GlobalRanking]:
    """Percentile position of the sample within the reference library."""
    rankings: list[GlobalRanking] = []
    for metric, value in metrics.items():
        label, _unit, lower_better = METRIC_META.get(metric, (metric, None, False))
        population: list[tuple[float, str]] = []
        for reference in references:
            ref_value = (reference.get("metrics") or {}).get(metric)
            if ref_value is None:
                continue
            population.append((float(ref_value), reference.get("label", reference.get("key", ""))))
        if len(population) < 3:
            continue
        values = [v for v, _ in population]
        # Percentile expressed so that 100 always means "best in the library",
        # whichever direction "better" runs for this metric.
        if lower_better:
            beaten = sum(1 for v in values if v > value)
        else:
            beaten = sum(1 for v in values if v < value)
        percentile = 100.0 * beaten / len(values)
        order = sorted(population, key=lambda item: item[0], reverse=not lower_better)
        rankings.append(
            GlobalRanking(
                metric=metric,
                label=label,
                sample_value=round(value, 4),
                percentile=round(percentile, 1),
                n_references=len(values),
                better_is_lower=lower_better,
                reference_values=[round(v, 4) for v, _ in order],
                reference_labels=[name for _, name in order],
            )
        )
    return rankings


def build_dft_overlay(
    properties: SampleProperties | None, reference_properties: list[dict]
) -> DFTResult | None:
    if properties is None:
        return None
    comparisons: list[PropertyComparison] = []
    notes: list[str] = []
    for key, (label, unit) in DFT_PROPERTIES.items():
        measured = getattr(properties, key, None)
        if measured is None:
            continue
        candidates = [
            (reference, float((reference.get("properties") or {})[key]))
            for reference in reference_properties
            if (reference.get("properties") or {}).get(key) is not None
        ]
        if not candidates:
            continue
        # Compare against the closest reference rather than an arbitrary first
        # match: mobility for suspended and supported graphene differ by 20x,
        # and flagging a supported device against the suspended record would be
        # misleading.
        reference, ref_value = min(candidates, key=lambda item: abs(item[1] - float(measured)))
        deviation = (
            (float(measured) - ref_value) / abs(ref_value) * 100.0 if ref_value else 0.0
        )
        magnitude = abs(deviation)
        verdict: Verdict = "pass" if magnitude <= 20 else "borderline" if magnitude <= 50 else "fail"
        if key == "band_gap_ev" and ref_value == 0.0:
            # A zero reference makes percentage deviation meaningless.
            deviation = 0.0 if measured == 0 else 100.0
            verdict = "pass" if float(measured) < 0.05 else "fail"
        comparisons.append(
            PropertyComparison(
                property=key,
                label=label,
                unit=unit,
                measured=float(measured),
                reference_value=ref_value,
                reference_material=reference.get("label", reference.get("key", "")),
                reference_source=(reference.get("provenance") or {}).get("source", ""),
                percent_deviation=round(deviation, 1),
                verdict=verdict,
            )
        )
    if not comparisons:
        return None
    notes.append(
        "Each measured property is compared against the closest matching reference "
        "record, which is named in the row. DFT values are ground-state, defect-free "
        "and at 0 K, so real material is expected to fall below them."
    )
    return DFTResult(comparisons=comparisons, notes=notes)


def collect_citations(records: list[dict]) -> list[dict]:
    """De-duplicated provenance blocks for the report bibliography."""
    seen: set[str] = set()
    citations: list[dict] = []
    for record in records:
        provenance = record.get("provenance")
        if not provenance:
            continue
        marker = f"{provenance.get('source')}|{provenance.get('doi')}|{provenance.get('url')}"
        if marker in seen:
            continue
        seen.add(marker)
        citations.append(
            {
                "source": provenance.get("source"),
                "doi": provenance.get("doi"),
                "url": provenance.get("url"),
                "version": provenance.get("version"),
                "retrieved": provenance.get("retrieved"),
                "used_for": record.get("label") or record.get("product_name") or record.get("key"),
            }
        )
    return citations


def build_report_payload(
    raman: RamanResult | None,
    xps: XPSResult | None,
    properties: SampleProperties | None,
    reference_spectra: list[dict],
    reference_properties: list[dict],
    applications: list[dict],
    products: list[dict],
) -> dict:
    classification = classify(raman, xps)
    metrics = collect_all_metrics(raman, xps, properties)

    scorecard = build_scorecard(classification.form, metrics)
    rankings = build_rankings(metrics, reference_spectra)
    app_matches = recommend_applications(applications, classification, raman, xps, properties)
    peer_matches = match_peers(products, classification, raman, xps, properties)
    dft = build_dft_overlay(properties, reference_properties)

    warnings = list(classification.warnings)
    if raman is None and xps is None:
        warnings.append("No spectra were analysed.")
    if properties is None or not any(
        getattr(properties, field, None) is not None
        for field in ("bet_m2_g", "flake_size_um")
    ):
        warnings.append(
            "No BET surface area or flake size was supplied. Several application "
            "criteria and the commercial peer distance depend on them, so those "
            "results are based on spectroscopy alone."
        )

    matched_products = {m.product_id for m in peer_matches}
    citations = collect_citations(
        reference_spectra
        + reference_properties
        + [p for p in products if p.get("key") in matched_products]
    )

    return {
        "engine_version": ENGINE_VERSION,
        "raman": raman,
        "xps": xps,
        "dft": dft,
        "classification": classification,
        "scorecard": scorecard,
        "rankings": rankings,
        "applications": app_matches,
        "peers": peer_matches,
        "citations": citations,
        "warnings": warnings,
    }
