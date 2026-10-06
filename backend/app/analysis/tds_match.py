"""Match a carbon's TDS specifications to applications, using the market.

The evidence is the application database: every commercial grade with the
applications its vendor sells it into and the specs its datasheet quotes.
For each application we derive the spec *envelope* the market covers (the
union of the intervals quoted by the products sold into it) and score how
the user's material sits inside it, weighted by which parameters matter
for that application. A nearest-product search inside the same evidence
set says which vendor grades the material most resembles.

Two ideas keep this honest. Everything is an interval, because datasheets
quote "20-50 um" and "<10 um" rather than numbers. And scores carry a
confidence that shrinks when an application has few products behind it or
when the user's sheet leaves most of the weighted parameters blank.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.analysis.specvalue import SpecValue, describe, parse_spec_value
from app.models.analysis import Verdict
from app.models.tds import (
    ApplicationFit,
    ApplicationProfile,
    Confidence,
    MarketPercentile,
    ParameterCheck,
    ProductDelta,
    ProductSimilarity,
    SpecInterval,
    TdsMatchReport,
    TdsSpecInput,
)

LAYER_SPACING_NM = 0.335


@dataclass(frozen=True)
class Parameter:
    key: str
    label: str
    unit: str
    log_scale: bool
    # Absolute tolerance (in transformed units) inside which a miss is only
    # borderline: 0.3 decades for log-scale parameters, a per-parameter
    # amount for linear ones.
    tolerance: float
    # Which direction the market usually treats as "better", for the
    # percentile commentary. None when it depends on the application.
    better: str | None = None
    hint: str = ""


PARAMETERS: dict[str, Parameter] = {
    p.key: p
    for p in (
        Parameter("lateral_size_um", "Lateral size", "um", True, 0.3, None, "Flake / platelet lateral dimension (D50 or range)"),
        Parameter("layers", "Number of layers", "", True, 0.3, "lower", "Average or range; 1 layer = 0.335 nm"),
        Parameter("thickness_nm", "Thickness", "nm", True, 0.3, "lower", "Used to infer layers when they are not quoted"),
        Parameter("bet_m2_g", "BET surface area", "m2/g", True, 0.3, None, "Specific surface area"),
        Parameter("carbon_purity_pct", "Carbon purity", "%", False, 1.5, "higher", "Carbon content by XPS / EDX / elemental analysis"),
        Parameter("oxygen_pct", "Oxygen content", "%", False, 1.5, "lower", "Atomic or weight % oxygen"),
        Parameter("impurities_pct", "Impurities", "%", False, 1.0, "lower", "Ash / metals / other elements"),
        Parameter("bulk_density_g_cm3", "Bulk density", "g/cm3", True, 0.3, None, "Tapped or apparent density of the powder"),
        Parameter("electrical_conductivity_s_m", "Electrical conductivity", "S/m", True, 0.4, "higher", "Powder or compact conductivity"),
        Parameter("thermal_conductivity_w_mk", "Thermal conductivity", "W/m.K", True, 0.4, "higher", "As quoted (in-plane values are common)"),
        Parameter("id_ig", "Raman I(D)/I(G)", "", False, 0.15, "lower", "Defect ratio"),
        Parameter("loading_wt_pct", "Graphene loading", "wt%", False, 5.0, None, "Solids in a dispersion, paste or masterbatch"),
        Parameter("density_g_ml", "Dispersion density", "g/mL", True, 0.1, None, "Density of the liquid product"),
        Parameter("viscosity_cps", "Viscosity", "cP", True, 0.4, None, "Of the ink / paste"),
        Parameter("sheet_resistance_ohm_sq", "Sheet resistance", "ohm/sq", True, 0.4, "lower", "Of a printed / coated layer"),
        Parameter("hydrogen_pct", "Hydrogen content", "%", False, 0.5, "lower", "From CHNS; recorded, not scored"),
        Parameter("thermal_stability_c", "Thermal stability (air)", "°C", False, 50.0, "higher", "TGA onset; recorded, not scored"),
    )
}

# How the two halves of the score combine, and how form disagreement is
# treated. Powder-to-paste is a formulation step rather than a different
# material, so a form mismatch trims the score without disqualifying.
ENVELOPE_WEIGHT = 0.65
PEER_WEIGHT = 0.35
FORM_MISMATCH_FACTOR = 0.9
MIN_SHARED_FOR_PEER = 2
MAX_PEER_DISTANCE = 3.0

PASS_FLOOR, PASS_CEILING = 0.70, 1.0
BORDERLINE_FLOOR, BORDERLINE_CEILING = 0.40, 0.65


# --------------------------------------------------------------------------- #
# Interval helpers
# --------------------------------------------------------------------------- #
def _transform(parameter: Parameter, value: float | None) -> float | None:
    if value is None:
        return None
    if parameter.log_scale:
        return math.log10(value) if value > 0 else None
    return float(value)


def _spec(product: dict, key: str) -> SpecValue | None:
    return SpecValue.from_dict((product.get("specs") or {}).get(key))


def _robust_spread(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    values = sorted(values)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    deviations = sorted(abs(v - median) for v in values)
    mad = deviations[mid] if len(deviations) % 2 else (deviations[mid - 1] + deviations[mid]) / 2
    return max(1.4826 * mad, 1e-6)


def collect_user_specs(spec: TdsSpecInput) -> tuple[dict[str, SpecValue], list[str]]:
    """Parse every entered field; infer layers from thickness if needed."""
    values: dict[str, SpecValue] = {}
    warnings: list[str] = []
    for key in PARAMETERS:
        raw = getattr(spec, key, None)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        parsed = parse_spec_value(raw)
        if parsed is None:
            warnings.append(f"Could not read a number from '{raw}' for {PARAMETERS[key].label}; ignored.")
            continue
        values[key] = parsed
    if "layers" not in values and "thickness_nm" in values:
        thickness = values["thickness_nm"]
        low = thickness.low / LAYER_SPACING_NM if thickness.low is not None else None
        high = thickness.high / LAYER_SPACING_NM if thickness.high is not None else None
        low = max(1.0, round(low)) if low is not None else None
        high = max(1.0, round(high)) if high is not None else None
        values["layers"] = SpecValue(low, high, f"{describe(thickness, 'nm')} / 0.335 nm")
        warnings.append("Layer count inferred from thickness at 0.335 nm per layer.")
    return values, warnings


# --------------------------------------------------------------------------- #
# Market envelope per application
# --------------------------------------------------------------------------- #
def build_envelope(products: list[dict], key: str, parameter: Parameter) -> dict | None:
    """Union of the intervals quoted for one parameter across products."""
    lows: list[float] = []
    highs: list[float] = []
    nominals: list[float] = []
    open_low = open_high = False
    n = 0
    for product in products:
        value = _spec(product, key)
        if value is None:
            continue
        n += 1
        if value.low is None:
            open_low = True
        else:
            lows.append(value.low)
        if value.high is None:
            open_high = True
        else:
            highs.append(value.high)
        nominal = value.nominal(parameter.log_scale)
        if nominal is not None:
            nominals.append(nominal)
    if n == 0:
        return None
    low = None if open_low or not lows else min(lows)
    high = None if open_high or not highs else max(highs)
    # An envelope open on both sides carries no information: fall back to the
    # spread of the quoted bounds so the check still says something.
    if low is None and high is None and nominals:
        low, high = min(nominals), max(nominals)
    nominals_sorted = sorted(nominals)
    typical = None
    if nominals_sorted:
        mid = len(nominals_sorted) // 2
        typical = (
            nominals_sorted[mid]
            if len(nominals_sorted) % 2
            else (nominals_sorted[mid - 1] + nominals_sorted[mid]) / 2
        )
    return {"low": low, "high": high, "typical": typical, "n": n, "text": describe(SpecValue(low, high, ""), parameter.unit)}


def _as_band(parameter: Parameter, value: SpecValue) -> tuple[float, float] | None:
    """(centre, half-width) in transformed space.

    A one-sided bound such as ">97 %" or "<10 um" is treated as the bound plus
    or minus the parameter tolerance rather than as an infinite interval:
    otherwise a vendor who quotes "<10 um" would look identical to everyone.
    """
    if value.low is not None and value.high is not None:
        low, high = _transform(parameter, value.low), _transform(parameter, value.high)
        if low is None or high is None:
            return None
        return (low + high) / 2.0, (high - low) / 2.0
    bound = value.low if value.low is not None else value.high
    centre = _transform(parameter, bound)
    if centre is None:
        return None
    # The band leans into the open side but keeps half a tolerance on the
    # closed side, since a vendor quoting ">3500 S/m" is selling material near
    # that figure: ">97" covers 96.25 to 99.25 with a 1.5-point tolerance.
    shift = 0.5 * parameter.tolerance if value.low is not None else -0.5 * parameter.tolerance
    return centre + shift, parameter.tolerance


def _bands_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= a[1] + b[1]


def _check_against_market(
    parameter: Parameter, user: SpecValue, evidence: list[dict], key: str, envelope: dict
) -> tuple[float, Verdict, str]:
    """Score one parameter by how many grades sold for the use overlap it.

    Inside the market hull, the score grades the fraction of grades whose
    quoted interval overlaps the user's; a value that lands in a gap between
    grades is borderline. Outside the hull, the distance to the nearest edge
    in transformed units decides between borderline and fail.
    """
    entered = describe(user, parameter.unit)
    market = envelope["text"]
    user_band = _as_band(parameter, user)
    if user_band is None:
        return 0.0, "unknown", f"{parameter.label} {entered} could not be placed on the market scale."

    bands: list[tuple[float, float]] = []
    for product in evidence:
        theirs = _spec(product, key)
        if theirs is None:
            continue
        band = _as_band(parameter, theirs)
        if band is not None:
            bands.append(band)
    if not bands:
        return 0.0, "unknown", f"No grade sold for this use quotes {parameter.label.lower()}."

    n = len(bands)
    support = sum(1 for band in bands if _bands_overlap(user_band, band))
    hull_low = min(c - h for c, h in bands)
    hull_high = max(c + h for c, h in bands)
    centre = user_band[0]

    if support > 0:
        fraction = support / n
        score = PASS_FLOOR + (PASS_CEILING - PASS_FLOOR) * math.sqrt(fraction)
        return (
            score,
            "pass",
            f"{parameter.label} {entered} overlaps {support} of {n} grades sold for this use (market {market}).",
        )

    if hull_low <= centre <= hull_high:
        return (
            BORDERLINE_CEILING,
            "borderline",
            f"{parameter.label} {entered} falls in a gap between the grades sold for this use (market {market}).",
        )

    if centre > hull_high:
        gap, side = centre - (hull_high), "above"
    else:
        gap, side = hull_low - centre, "below"
    # The user's own half-width already counts toward overlap, so measure the
    # remaining gap from the edge of their band.
    gap = max(0.0, gap - user_band[1])
    ratio = gap / parameter.tolerance
    how = _describe_gap(parameter, gap, side)
    if ratio <= 1.0:
        score = BORDERLINE_CEILING - (BORDERLINE_CEILING - BORDERLINE_FLOOR) * ratio
        return (
            score,
            "borderline",
            f"{parameter.label} {entered} is just {side} the {market} sold for this use ({how}).",
        )
    score = max(0.0, BORDERLINE_FLOOR * (1.0 - (ratio - 1.0) / 2.0))
    return score, "fail", f"{parameter.label} {entered} is {side} the {market} sold for this use ({how})."


def _describe_gap(parameter: Parameter, gap: float, side: str) -> str:
    if parameter.log_scale:
        factor = 10**gap
        return f"{factor:.1f}x {'larger' if side == 'above' else 'smaller'} than the edge"
    unit = f" {parameter.unit}" if parameter.unit else ""
    return f"{gap:.3g}{unit} {'over' if side == 'above' else 'under'} the edge"


# --------------------------------------------------------------------------- #
# Nearest products
# --------------------------------------------------------------------------- #
def _feature_scales(products: list[dict]) -> dict[str, float]:
    scales: dict[str, float] = {}
    for key, parameter in PARAMETERS.items():
        population = []
        for product in products:
            value = _spec(product, key)
            if value is None:
                continue
            transformed = _transform(parameter, value.nominal(parameter.log_scale))
            if transformed is not None:
                population.append(transformed)
        scales[key] = _robust_spread(population)
    return scales


def _interval_distance(parameter: Parameter, a: SpecValue, b: SpecValue) -> float | None:
    """Zero when the bands overlap, else the gap between their nearest edges."""
    band_a, band_b = _as_band(parameter, a), _as_band(parameter, b)
    if band_a is None or band_b is None:
        return None
    return max(0.0, abs(band_a[0] - band_b[0]) - band_a[1] - band_b[1])


def rank_products(
    user: dict[str, SpecValue],
    products: list[dict],
    weights: dict[str, float] | None,
    scales: dict[str, float],
    top_n: int,
) -> list[ProductSimilarity]:
    scored: list[tuple[float, ProductSimilarity]] = []
    for product in products:
        weighted = total = 0.0
        shared: list[str] = []
        deltas: list[ProductDelta] = []
        for key, parameter in PARAMETERS.items():
            if key not in user:
                continue
            theirs = _spec(product, key)
            if theirs is None:
                continue
            distance = _interval_distance(parameter, user[key], theirs)
            if distance is None:
                continue
            weight = (weights or {}).get(key, 0.5)
            weighted += weight * min(distance / scales.get(key, 1.0), MAX_PEER_DISTANCE)
            total += weight
            shared.append(parameter.label)
            yours_nominal = user[key].nominal(parameter.log_scale)
            theirs_nominal = theirs.nominal(parameter.log_scale)
            if distance == 0.0:
                relation = "within"
            elif yours_nominal is not None and theirs_nominal is not None and yours_nominal > theirs_nominal:
                relation = "higher"
            else:
                relation = "lower"
            deltas.append(
                ProductDelta(
                    parameter=key,
                    label=parameter.label,
                    unit=parameter.unit,
                    yours=describe(user[key], parameter.unit),
                    theirs=describe(theirs, parameter.unit),
                    relation=relation,
                )
            )
        if len(shared) < MIN_SHARED_FOR_PEER or total <= 0:
            continue
        distance = weighted / total
        similarity = 100.0 * math.exp(-distance)
        differing = [d for d in deltas if d.relation != "within"]
        if differing:
            phrase = "; ".join(f"{d.label.lower()} {d.relation} ({d.yours} vs {d.theirs})" for d in differing[:2])
            summary = f"Differs on {phrase}."
        else:
            summary = "Every shared parameter overlaps this grade's datasheet."
        scored.append(
            (
                distance,
                ProductSimilarity(
                    key=product["key"],
                    company=product["company"],
                    product=product["product"],
                    form=product.get("form", "unknown"),
                    similarity_pct=round(similarity, 1),
                    shared_parameters=shared,
                    deltas=deltas,
                    application_tags=product.get("application_tags") or [],
                    applications_text=product.get("applications_text"),
                    datasheet_url=product.get("datasheet_url"),
                    datasheet_file=product.get("datasheet_file"),
                    summary=summary,
                ),
            )
        )
    # Equal distances are common when a vendor quotes only two or three broad
    # specs; the grade that was compared on more parameters ranks first.
    scored.sort(key=lambda item: (item[0], -len(item[1].shared_parameters)))
    return [match for _, match in scored[:top_n]]


# --------------------------------------------------------------------------- #
# Application profiles and fits
# --------------------------------------------------------------------------- #
def build_profiles(taxonomy: list[dict], products: list[dict]) -> list[ApplicationProfile]:
    profiles: list[ApplicationProfile] = []
    for entry in taxonomy:
        evidence = [p for p in products if entry["key"] in (p.get("application_tags") or [])]
        envelopes = {}
        for key, parameter in PARAMETERS.items():
            envelope = build_envelope(evidence, key, parameter)
            if envelope is not None:
                envelopes[key] = envelope
        profiles.append(
            ApplicationProfile(
                key=entry["key"],
                name=entry["name"],
                description=entry.get("description", ""),
                evidence_count=len(evidence),
                companies=sorted({p["company"] for p in evidence}),
                products=[f"{p['company']} {p['product']}" for p in evidence],
                parameter_weights=entry.get("parameter_weights", {}),
                envelopes=envelopes,
            )
        )
    return profiles


def _confidence(evidence_count: int, coverage: float) -> Confidence:
    if evidence_count >= 5 and coverage >= 0.6:
        return "high"
    if evidence_count >= 3 and coverage >= 0.4:
        return "medium"
    return "low"


def match_applications(
    spec: TdsSpecInput,
    taxonomy: list[dict],
    products: list[dict],
    market: list[dict] | None = None,
    top_products: int = 3,
) -> TdsMatchReport:
    user, warnings = collect_user_specs(spec)
    scales = _feature_scales(products)
    fits: list[ApplicationFit] = []

    if not user:
        warnings.append("No numeric specification was entered, so applications cannot be ranked.")

    for entry in taxonomy:
        evidence = [p for p in products if entry["key"] in (p.get("application_tags") or [])]
        weights: dict[str, float] = entry.get("parameter_weights", {})
        checks: list[ParameterCheck] = []
        weighted = total = 0.0
        total_possible = sum(weights.values()) or 1.0

        for key, weight in weights.items():
            parameter = PARAMETERS[key]
            envelope = build_envelope(evidence, key, parameter)
            if key not in user:
                continue
            if envelope is None:
                checks.append(
                    ParameterCheck(
                        parameter=key, label=parameter.label, unit=parameter.unit,
                        entered=describe(user[key], parameter.unit), market_range="no vendor quotes it",
                        typical=None, n_products=0, weight=weight, score=0.0, verdict="unknown",
                        comment=f"No product sold for this use quotes {parameter.label.lower()}, so it is not scored.",
                    )
                )
                continue
            score, verdict, comment = _check_against_market(parameter, user[key], evidence, key, envelope)
            if verdict != "unknown":
                weighted += weight * score
                total += weight
            checks.append(
                ParameterCheck(
                    parameter=key, label=parameter.label, unit=parameter.unit,
                    entered=describe(user[key], parameter.unit), market_range=envelope["text"],
                    typical=round(envelope["typical"], 4) if envelope["typical"] is not None else None,
                    n_products=envelope["n"], weight=weight, score=round(score, 3), verdict=verdict, comment=comment,
                )
            )

        envelope_fit = weighted / total if total > 0 else 0.0
        coverage = total / total_possible
        peers = rank_products(user, evidence, weights, scales, top_products) if evidence else []
        peer_fit = (peers[0].similarity_pct / 100.0) if peers else envelope_fit

        preferred = entry.get("preferred_forms") or []
        evidence_forms = {p.get("form") for p in evidence}
        form_match = spec.form == "unknown" or spec.form in preferred or spec.form in evidence_forms
        form_factor = 1.0 if form_match else FORM_MISMATCH_FACTOR

        if total > 0:
            score = 100.0 * (ENVELOPE_WEIGHT * envelope_fit + PEER_WEIGHT * peer_fit) * form_factor
        else:
            score = 0.0
        # Thin evidence or thin coverage caps how confident a high score can look.
        evidence_factor = min(1.0, 0.6 + 0.1 * len(evidence))
        score *= evidence_factor if total > 0 else 1.0

        evaluated = [c for c in checks if c.verdict != "unknown"]
        if not evaluated:
            verdict: Verdict = "unknown"
        elif any(c.verdict == "fail" for c in evaluated):
            verdict = "fail"
        elif any(c.verdict == "borderline" for c in evaluated):
            verdict = "borderline"
        else:
            verdict = "pass"

        strengths = [c.comment for c in evaluated if c.verdict == "pass"]
        gaps = [c.comment for c in evaluated if c.verdict in {"fail", "borderline"}]
        if not form_match and spec.form != "unknown":
            gaps.append(
                f"Sold as {spec.form}, while products for this use ship as "
                + ", ".join(sorted(f for f in evidence_forms if f)) + "."
            )
        unassessed = [PARAMETERS[k].label for k in weights if k not in user]

        fits.append(
            ApplicationFit(
                key=entry["key"],
                name=entry["name"],
                description=entry.get("description", ""),
                score=round(score, 1),
                verdict=verdict,
                confidence=_confidence(len(evidence), coverage),
                evidence_count=len(evidence),
                coverage=round(coverage, 3),
                form_match=form_match,
                checks=checks,
                closest_products=peers,
                strengths=strengths,
                gaps=gaps,
                rationale=_rationale(entry, evidence, evaluated, gaps, unassessed, peers),
            )
        )

    fits.sort(key=lambda f: (f.score, f.evidence_count), reverse=True)
    closest = rank_products(user, products, None, scales, 5) if user else []
    context = market_percentiles(user, market or [])
    return TdsMatchReport(
        name=spec.name,
        form=spec.form,
        entered={k: SpecInterval(low=v.low, high=v.high, text=describe(v, PARAMETERS[k].unit)) for k, v in user.items()},
        applications=fits,
        closest_products=closest,
        market_context=context,
        warnings=warnings,
        database_size=len(products),
        market_size=len(market or []),
    )


def _rationale(
    entry: dict,
    evidence: list[dict],
    evaluated: list[ParameterCheck],
    gaps: list[str],
    unassessed: list[str],
    peers: list[ProductSimilarity],
) -> str:
    parts: list[str] = []
    companies = sorted({p["company"] for p in evidence})
    if evidence:
        parts.append(
            f"{len(evidence)} commercial grade{'s' if len(evidence) != 1 else ''} from "
            f"{', '.join(companies[:4])}{' and others' if len(companies) > 4 else ''} "
            f"are sold into this use."
        )
    else:
        parts.append("No product in the database is sold into this use yet, so there is no market envelope to compare against.")
    passed = [c.label for c in evaluated if c.verdict == "pass"]
    if passed:
        parts.append("Within the market range on " + ", ".join(passed) + ".")
    if gaps:
        parts.append(gaps[0])
    if peers:
        best = peers[0]
        parts.append(f"Closest grade sold for this use: {best.company} {best.product} ({best.similarity_pct:.0f}% similar).")
    if unassessed:
        parts.append("Not assessed on " + ", ".join(unassessed) + " (not on your sheet).")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Market percentiles from the literature survey
# --------------------------------------------------------------------------- #
def market_percentiles(user: dict[str, SpecValue], market: list[dict]) -> list[MarketPercentile]:
    results: list[MarketPercentile] = []
    if not market:
        return results
    for key, value in user.items():
        parameter = PARAMETERS[key]
        nominal = value.nominal(parameter.log_scale)
        if nominal is None:
            continue
        population = []
        for record in market:
            theirs = SpecValue.from_dict((record.get("specs") or {}).get(key))
            if theirs is None:
                continue
            their_nominal = theirs.nominal(parameter.log_scale)
            if their_nominal is not None:
                population.append(their_nominal)
        if len(population) < 5:
            continue
        below = sum(1 for v in population if v < nominal)
        equal = sum(1 for v in population if v == nominal)
        percentile = 100.0 * (below + 0.5 * equal) / len(population)
        if parameter.better == "higher":
            comment = f"Higher than {percentile:.0f}% of {len(population)} commercial grades that quote it."
        elif parameter.better == "lower":
            comment = f"Lower than {100 - percentile:.0f}% of {len(population)} commercial grades that quote it."
        else:
            comment = f"At the {percentile:.0f}th percentile of {len(population)} commercial grades that quote it."
        results.append(
            MarketPercentile(
                parameter=key, label=parameter.label, unit=parameter.unit,
                value=round(nominal, 4), percentile=round(percentile, 1),
                n_products=len(population), comment=comment,
            )
        )
    return results


def parameter_metadata() -> list[dict]:
    return [
        {"key": p.key, "label": p.label, "unit": p.unit, "hint": p.hint, "log_scale": p.log_scale}
        for p in PARAMETERS.values()
    ]
