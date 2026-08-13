"""Module B - nearest commercial products by weighted spec distance."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.analysis import (
    Classification,
    PeerFeatureDelta,
    PeerMatch,
    RamanResult,
    XPSResult,
)
from app.models.sample import SampleProperties


@dataclass(frozen=True)
class _Feature:
    key: str
    label: str
    weight: float
    log_scale: bool
    # Phrase used when describing a difference, and whether a higher sample
    # value means "more" of that phrase.
    phrase: str
    invert_phrase: bool = False


FEATURES: tuple[_Feature, ...] = (
    _Feature("id_ig", "I(D)/I(G)", 1.5, False, "defect density"),
    _Feature("co_ratio", "C/O ratio", 1.3, True, "oxygen content", invert_phrase=True),
    _Feature("flake_size_um", "Flake size", 1.2, True, "flake size"),
    _Feature("bet_m2_g", "BET surface area", 1.0, True, "surface area"),
    _Feature("i2d_ig", "I(2D)/I(G)", 0.8, False, "2D/G intensity ratio"),
    _Feature("layers", "Layer count", 0.8, True, "layer count"),
)

# Product forms that a given classified form can sensibly be compared against.
FORM_COMPATIBILITY: dict[str, set[str]] = {
    "monolayer": {"cvd_monolayer_film"},
    "bilayer": {"cvd_monolayer_film"},
    "few_layer": {"gnp_few_layer", "ink_dispersion", "flash_turbostratic"},
    "multilayer_gnp": {"gnp_few_layer", "ink_dispersion"},
    "graphene_oxide": {"graphene_oxide"},
    "reduced_graphene_oxide": {"rgo", "ink_dispersion"},
    "turbostratic": {"flash_turbostratic", "gnp_few_layer"},
    "amorphous_carbon": set(),
    "indeterminate": set(),
}

# Added to the distance when the product is a different physical form. Large
# enough that same-form products win on ties, small enough that a very close
# spec match in an adjacent form can still surface.
FORM_MISMATCH_PENALTY = 0.75
MIN_SHARED_FEATURES = 2
MAX_FEATURE_DISTANCE = 3.0


def _transform(feature: _Feature, value: float) -> float | None:
    if value is None or value <= 0:
        return None if feature.log_scale else float(value)
    return math.log10(value) if feature.log_scale else float(value)


def _robust_spread(values: list[float]) -> float:
    """Median absolute deviation, scaled to a standard-deviation equivalent."""
    if len(values) < 2:
        return 1.0
    values = sorted(values)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    deviations = sorted(abs(v - median) for v in values)
    mad = (
        deviations[mid]
        if len(deviations) % 2
        else (deviations[mid - 1] + deviations[mid]) / 2
    )
    return max(1.4826 * mad, 1e-6)


def build_sample_specs(
    raman: RamanResult | None,
    xps: XPSResult | None,
    properties: SampleProperties | None,
) -> dict[str, float]:
    specs: dict[str, float] = {}
    if raman is not None:
        if raman.id_ig is not None:
            specs["id_ig"] = float(raman.id_ig)
        if raman.i2d_ig is not None:
            specs["i2d_ig"] = float(raman.i2d_ig)
        layers = _layers_to_number(raman.estimated_layers)
        if layers is not None:
            specs["layers"] = layers
    if xps is not None and xps.co_ratio is not None:
        specs["co_ratio"] = float(xps.co_ratio)
    if properties is not None:
        for key in ("flake_size_um", "bet_m2_g"):
            value = getattr(properties, key, None)
            if value is not None:
                specs[key] = float(value)
    return specs


def _layers_to_number(estimate: str | None) -> float | None:
    return {"1L": 1.0, "2L": 2.0, "3-5L": 4.0, ">5L": 10.0}.get(estimate or "")


def match_peers(
    products: list[dict],
    classification: Classification,
    raman: RamanResult | None,
    xps: XPSResult | None,
    properties: SampleProperties | None,
    top_n: int = 3,
) -> list[PeerMatch]:
    sample = build_sample_specs(raman, xps, properties)
    if not sample or not products:
        return []

    # Normalise each feature by its spread across the catalogue, so a 0.1
    # difference in I(D)/I(G) and a 100 m2/g difference in BET are weighed
    # against how much products actually vary in each.
    scales: dict[str, float] = {}
    for feature in FEATURES:
        population = []
        for product in products:
            raw = (product.get("specs") or {}).get(feature.key)
            if raw is None:
                continue
            transformed = _transform(feature, float(raw))
            if transformed is not None:
                population.append(transformed)
        scales[feature.key] = _robust_spread(population)

    compatible = FORM_COMPATIBILITY.get(classification.form, set())
    scored: list[tuple[float, PeerMatch]] = []

    for product in products:
        specs = product.get("specs") or {}
        deltas: list[PeerFeatureDelta] = []
        weighted = 0.0
        total_weight = 0.0
        compared: list[str] = []

        for feature in FEATURES:
            if feature.key not in sample or feature.key not in specs:
                continue
            sample_value = float(sample[feature.key])
            product_value = float(specs[feature.key])
            a = _transform(feature, sample_value)
            b = _transform(feature, product_value)
            if a is None or b is None:
                continue
            distance = min(abs(a - b) / scales[feature.key], MAX_FEATURE_DISTANCE)
            weighted += feature.weight * distance
            total_weight += feature.weight
            compared.append(feature.label)
            percent = (
                (sample_value - product_value) / product_value * 100.0
                if product_value
                else 0.0
            )
            deltas.append(
                PeerFeatureDelta(
                    feature=feature.key,
                    label=feature.label,
                    sample_value=round(sample_value, 4),
                    product_value=round(product_value, 4),
                    percent_difference=round(percent, 1),
                    direction="higher" if percent > 0 else "lower" if percent < 0 else "equal",
                )
            )

        if len(compared) < MIN_SHARED_FEATURES or total_weight <= 0:
            continue

        distance = weighted / total_weight
        form_mismatch = bool(compatible) and product.get("form") not in compatible
        if form_mismatch:
            distance += FORM_MISMATCH_PENALTY

        similarity = 100.0 * math.exp(-distance)
        summary = _summarise(product, deltas, form_mismatch, classification)

        scored.append(
            (
                distance,
                PeerMatch(
                    product_id=product.get("key", ""),
                    producer=product.get("producer", "Unknown"),
                    product_name=product.get("product_name", "Unknown"),
                    form=product.get("form", "unknown"),
                    region=product.get("region"),
                    datasheet_url=product.get("datasheet_url"),
                    distance=round(distance, 4),
                    similarity_pct=round(similarity, 1),
                    features_compared=compared,
                    deltas=deltas,
                    summary=summary,
                ),
            )
        )

    scored.sort(key=lambda item: item[0])
    return [match for _, match in scored[:top_n]]


def _summarise(
    product: dict,
    deltas: list[PeerFeatureDelta],
    form_mismatch: bool,
    classification: Classification,
) -> str:
    lookup = {f.key: f for f in FEATURES}
    ranked = sorted(deltas, key=lambda d: abs(d.percent_difference), reverse=True)
    phrases: list[str] = []
    for delta in ranked[:2]:
        feature = lookup[delta.feature]
        if feature.invert_phrase:
            # C/O is a ratio of carbon to oxygen, so the statement is about the
            # reciprocal. Re-deriving the percentage on 1/x rather than negating
            # the one computed on x avoids claiming something is "136% lower",
            # which is not a quantity that exists.
            if delta.sample_value <= 0 or delta.product_value <= 0:
                continue
            percent = (delta.product_value / delta.sample_value - 1.0) * 100.0
        else:
            percent = delta.percent_difference
        magnitude = abs(percent)
        if magnitude < 1.0:
            phrases.append(f"essentially identical {feature.phrase}")
            continue
        phrases.append(
            f"{magnitude:.0f}% {'higher' if percent > 0 else 'lower'} {feature.phrase}"
        )

    name = f"{product.get('producer', '')} {product.get('product_name', '')}".strip()
    text = f"Closest commercial peer: {name}"
    if phrases:
        text += " (" + ", ".join(phrases) + ")"
    text += "."
    if form_mismatch:
        text += (
            f" Note this product is sold as {str(product.get('form', 'unknown')).replace('_', ' ')}, "
            f"while the sample classifies as {classification.label.lower()}; it is the "
            "closest spec match rather than a like-for-like substitute."
        )
    return text
