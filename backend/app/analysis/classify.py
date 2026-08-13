"""Rule-based classification of graphene form from Raman + XPS metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models.analysis import (
    FORM_LABELS,
    Classification,
    ClassificationEvidence,
    MaterialForm,
    RamanResult,
    XPSResult,
)


def trapezoid(value: float, out_lo: float, in_lo: float, in_hi: float, out_hi: float) -> float:
    """Fuzzy membership: 1 inside [in_lo, in_hi], ramping to 0 at the outer edges."""
    if value <= out_lo or value >= out_hi:
        return 0.0
    if in_lo <= value <= in_hi:
        return 1.0
    if value < in_lo:
        return (value - out_lo) / (in_lo - out_lo) if in_lo > out_lo else 1.0
    return (out_hi - value) / (out_hi - in_hi) if out_hi > in_hi else 1.0


@dataclass
class _Rule:
    metric: str
    weight: float
    detail: str
    score: Callable[[dict], float | None]


def _get(name: str) -> Callable[[dict], float | None]:
    return lambda facts: facts.get(name)


def _band(name: str, out_lo: float, in_lo: float, in_hi: float, out_hi: float):
    def scorer(facts: dict) -> float | None:
        value = facts.get(name)
        if value is None:
            return None
        return trapezoid(float(value), out_lo, in_lo, in_hi, out_hi)

    return scorer


def _equals(name: str, target) -> Callable[[dict], float | None]:
    def scorer(facts: dict) -> float | None:
        value = facts.get(name)
        if value is None:
            return None
        return 1.0 if value == target else 0.0

    return scorer


def _is_true(name: str) -> Callable[[dict], float | None]:
    def scorer(facts: dict) -> float | None:
        value = facts.get(name)
        if value is None:
            return None
        return 1.0 if value else 0.0

    return scorer


# Each form is described by the evidence a spectroscopist would actually cite.
RULES: dict[MaterialForm, list[_Rule]] = {
    "monolayer": [
        _Rule("layers", 3.0, "Raman layer estimate is 1L", _equals("layers", "1L")),
        _Rule("fwhm_2d_cm1", 2.0, "2D band narrower than ~40 cm-1", _band("fwhm_2d_cm1", 0, 0, 38, 48)),
        _Rule("i2d_ig", 2.0, "I(2D)/I(G) above ~1.6", _band("i2d_ig", 1.0, 1.7, 10.0, 10.0)),
        _Rule("id_ig", 1.5, "Low defect density", _band("id_ig", -1, -1, 0.15, 0.5)),
        _Rule("co_ratio", 1.0, "Low oxygen content", _band("co_ratio", 8, 15, 1e6, 1e6)),
    ],
    "bilayer": [
        _Rule("layers", 3.0, "Raman layer estimate is 2L", _equals("layers", "2L")),
        _Rule("fwhm_2d_cm1", 2.0, "2D band ~40-58 cm-1", _band("fwhm_2d_cm1", 32, 42, 58, 68)),
        _Rule("i2d_ig", 1.5, "I(2D)/I(G) around 1", _band("i2d_ig", 0.4, 0.8, 2.0, 2.8)),
        _Rule("co_ratio", 1.0, "Low oxygen content", _band("co_ratio", 8, 15, 1e6, 1e6)),
    ],
    "few_layer": [
        _Rule("layers", 3.0, "Raman layer estimate is 3-5L", _equals("layers", "3-5L")),
        _Rule("fwhm_2d_cm1", 2.0, "2D band ~55-75 cm-1", _band("fwhm_2d_cm1", 45, 56, 74, 85)),
        _Rule("i2d_ig", 1.5, "I(2D)/I(G) between 0.3 and 1", _band("i2d_ig", 0.15, 0.3, 1.0, 1.6)),
        _Rule("id_ig", 1.0, "Moderate defect density", _band("id_ig", -1, -1, 0.6, 1.0)),
        _Rule("co_ratio", 1.0, "Low oxygen content", _band("co_ratio", 8, 15, 1e6, 1e6)),
    ],
    "multilayer_gnp": [
        _Rule("layers", 3.0, "Raman layer estimate is >5L", _equals("layers", ">5L")),
        _Rule("fwhm_2d_cm1", 2.0, "Broad 2D band above ~70 cm-1", _band("fwhm_2d_cm1", 60, 74, 1e6, 1e6)),
        _Rule("i2d_ig", 1.5, "Weak 2D band", _band("i2d_ig", -1, -1, 0.45, 0.8)),
        _Rule("co_ratio", 1.0, "Low oxygen content", _band("co_ratio", 8, 15, 1e6, 1e6)),
    ],
    "graphene_oxide": [
        _Rule("co_ratio", 4.0, "C/O between 1.5 and 4", _band("co_ratio", 1.0, 1.5, 4.0, 6.0)),
        _Rule("id_ig", 2.0, "I(D)/I(G) near 1", _band("id_ig", 0.4, 0.7, 1.5, 2.0)),
        _Rule("two_d_absent", 2.0, "2D band quenched", _is_true("two_d_absent")),
        _Rule("sp2_fraction", 2.0, "sp2 fraction below ~0.5", _band("sp2_fraction", 0.0, 0.0, 0.45, 0.65)),
        _Rule("oxygen_at_pct", 1.0, "High oxygen at%", _band("oxygen_at_pct", 12, 20, 100, 100)),
    ],
    "reduced_graphene_oxide": [
        _Rule("co_ratio", 4.0, "C/O between 5 and 15", _band("co_ratio", 3.5, 5.0, 15.0, 19.0)),
        _Rule("id_ig", 2.0, "I(D)/I(G) between 0.8 and 1.4", _band("id_ig", 0.5, 0.8, 1.4, 1.8)),
        _Rule("sp2_fraction", 1.5, "sp2 fraction 0.5-0.85", _band("sp2_fraction", 0.35, 0.5, 0.85, 0.95)),
        _Rule("two_d_weak", 1.0, "2D band weak or absent", _is_true("two_d_weak")),
    ],
    "turbostratic": [
        _Rule("two_d_single_lorentzian", 3.0, "2D band is a single symmetric Lorentzian", _is_true("two_d_single_lorentzian")),
        _Rule("multi_layer", 2.0, "More than one layer", _is_true("multi_layer")),
        _Rule("fwhm_2d_cm1", 1.5, "2D band ~45-75 cm-1", _band("fwhm_2d_cm1", 38, 45, 75, 90)),
        _Rule("id_ig", 1.5, "Low defect density despite thickness", _band("id_ig", -1, -1, 0.4, 0.8)),
        _Rule("co_ratio", 1.0, "Low oxygen content", _band("co_ratio", 10, 18, 1e6, 1e6)),
    ],
    "amorphous_carbon": [
        _Rule("id_ig", 3.0, "I(D)/I(G) above ~1.4", _band("id_ig", 1.0, 1.5, 1e6, 1e6)),
        _Rule("two_d_absent", 1.5, "No 2D band", _is_true("two_d_absent")),
        _Rule("fwhm_g_cm1", 1.5, "Very broad G band", _band("fwhm_g_cm1", 50, 80, 1e6, 1e6)),
        _Rule("sp2_fraction", 1.0, "Low sp2 fraction", _band("sp2_fraction", 0.0, 0.0, 0.5, 0.7)),
    ],
}


def build_facts(raman: RamanResult | None, xps: XPSResult | None) -> dict:
    """Flatten the two analyses into the metric namespace the rules address."""
    facts: dict = {}
    if raman is not None:
        facts["id_ig"] = raman.id_ig
        facts["i2d_ig"] = raman.i2d_ig
        facts["fwhm_2d_cm1"] = raman.fwhm_2d_cm1
        facts["fwhm_g_cm1"] = raman.fwhm_g_cm1
        facts["layers"] = raman.estimated_layers
        facts["two_d_single_lorentzian"] = raman.two_d_single_lorentzian
        has_2d = raman.fwhm_2d_cm1 is not None
        # Only assert "no 2D band" when the spectrum actually covered that
        # region - a spectrum truncated at 1800 cm-1 says nothing either way.
        covered_second_order = has_2d or any(
            "second-order" in note or "2D band rejected" in note for note in raman.notes
        )
        if covered_second_order:
            facts["two_d_absent"] = not has_2d
            facts["two_d_weak"] = (not has_2d) or (
                raman.i2d_ig is not None and raman.i2d_ig < 0.3
            )
        if raman.estimated_layers is not None:
            facts["multi_layer"] = raman.estimated_layers != "1L"
    if xps is not None:
        facts["co_ratio"] = xps.co_ratio
        facts["sp2_fraction"] = xps.sp2_fraction
        facts["oxygen_at_pct"] = xps.oxygen_at_pct
    return {k: v for k, v in facts.items() if v is not None}


def classify(raman: RamanResult | None, xps: XPSResult | None) -> Classification:
    facts = build_facts(raman, xps)
    warnings: list[str] = []

    if not facts:
        return Classification(
            form="indeterminate",
            label=FORM_LABELS["indeterminate"],
            confidence=0.0,
            warnings=["No usable Raman or XPS metrics were produced, so the sample could not be classified."],
        )

    scores: dict[str, float] = {}
    evidence_by_form: dict[str, list[ClassificationEvidence]] = {}

    for form, rules in RULES.items():
        total_weight = 0.0
        earned = 0.0
        items: list[ClassificationEvidence] = []
        for rule in rules:
            value = rule.score(facts)
            if value is None:
                continue
            total_weight += rule.weight
            earned += rule.weight * value
            raw = facts.get(rule.metric)
            items.append(
                ClassificationEvidence(
                    metric=rule.metric,
                    value=float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None,
                    supports=form,
                    weight=rule.weight * value,
                    detail=rule.detail,
                )
            )
        if total_weight <= 0:
            continue
        # Normalising by the weight of the evidence that was actually available
        # keeps forms comparable when a sample has Raman but no XPS. The
        # coverage term then stops a form scoring 1.0 off a single data point.
        coverage = total_weight / sum(r.weight for r in rules)
        scores[form] = (earned / total_weight) * (0.6 + 0.4 * coverage)
        evidence_by_form[form] = items

    if not scores:
        return Classification(
            form="indeterminate",
            label=FORM_LABELS["indeterminate"],
            confidence=0.0,
            warnings=["Available metrics did not match any known graphene form."],
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_form, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence combines how well the winner fits with how clearly it beats
    # the next candidate; a near-tie should never read as certain.
    margin = (best_score - runner_up) / best_score if best_score > 0 else 0.0
    confidence = float(min(1.0, best_score * (0.55 + 0.45 * min(margin / 0.3, 1.0))))

    if best_score < 0.45:
        warnings.append(
            "No form matched strongly. The metrics may be internally inconsistent, "
            "or the material may be a blend or a composite."
        )
    if margin < 0.08 and len(ranked) > 1:
        warnings.append(
            f"{FORM_LABELS[best_form]} and {FORM_LABELS[ranked[1][0]]} score almost "
            "identically; the available data cannot separate them."
        )
    if "co_ratio" not in facts:
        warnings.append(
            "No XPS data was supplied, so oxygen content is unknown. Raman alone "
            "cannot reliably separate rGO from defective few-layer graphene - both "
            "show a strong D band."
        )
    if raman is None:
        warnings.append(
            "No Raman data was supplied, so layer count and defect density are "
            "unknown and the form was inferred from chemistry alone."
        )

    form: MaterialForm = best_form  # type: ignore[assignment]
    return Classification(
        form=form,
        label=FORM_LABELS[form],
        confidence=round(confidence, 3),
        scores={k: round(v, 3) for k, v in ranked},
        evidence=sorted(evidence_by_form[best_form], key=lambda e: e.weight, reverse=True),
        layer_estimate=facts.get("layers"),
        warnings=warnings,
    )
