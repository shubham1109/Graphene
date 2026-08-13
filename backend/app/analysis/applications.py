"""Module A - match a classified sample to its best-fit applications."""

from __future__ import annotations

from app.models.analysis import (
    ApplicationMatch,
    Classification,
    CriterionCheck,
    RamanResult,
    Verdict,
    XPSResult,
)
from app.models.sample import SampleProperties

METRIC_LABELS = {
    "id_ig": ("I(D)/I(G)", ""),
    "i2d_ig": ("I(2D)/I(G)", ""),
    "fwhm_2d_cm1": ("FWHM(2D)", "cm-1"),
    "co_ratio": ("C/O ratio", ""),
    "bet_m2_g": ("BET surface area", "m2/g"),
    "flake_size_um": ("Flake size", "um"),
}

# How far outside a target window a value may sit before it fails outright.
BORDERLINE_TOLERANCE = 0.30


def collect_metrics(
    raman: RamanResult | None,
    xps: XPSResult | None,
    properties: SampleProperties | None,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if raman is not None:
        for name in ("id_ig", "i2d_ig", "fwhm_2d_cm1"):
            value = getattr(raman, name)
            if value is not None:
                metrics[name] = float(value)
    if xps is not None and xps.co_ratio is not None:
        metrics["co_ratio"] = float(xps.co_ratio)
    if properties is not None:
        for name in ("bet_m2_g", "flake_size_um"):
            value = getattr(properties, name, None)
            if value is not None:
                metrics[name] = float(value)
    return metrics


def _describe_target(spec: dict) -> str:
    low, high = spec.get("min"), spec.get("max")
    if low is not None and high is not None:
        return f"{low:g} - {high:g}"
    if low is not None:
        return f"> {low:g}"
    if high is not None:
        return f"< {high:g}"
    return "any"


# Score bands, kept disjoint so a comfortable pass always outranks a marginal
# one and no borderline result can score as high as any pass.
PASS_FLOOR, PASS_CEILING = 0.70, 1.0
BORDERLINE_FLOOR, BORDERLINE_CEILING = 0.40, 0.65


def _pass_score(value: float, low: float | None, high: float | None) -> float:
    """How comfortably a passing value sits inside its target window.

    A binary 1.0 for every pass makes applications tie at exactly 100, which
    renders the "top three" ordering arbitrary. Grading the margin means the
    application whose window the sample sits most centrally in comes first.
    """
    if low is not None and high is not None:
        centre = (low + high) / 2.0
        half_width = (high - low) / 2.0
        if half_width <= 0:
            return PASS_CEILING
        offset = min(1.0, abs(value - centre) / half_width)
        return PASS_CEILING - (PASS_CEILING - PASS_FLOOR) * offset
    bound = low if low is not None else high
    if bound is None or bound == 0:
        return PASS_CEILING
    # One-sided: reward clearing the bound by a wide margin, saturating at 2x.
    margin = (value - bound) / abs(bound) if low is not None else (bound - value) / abs(bound)
    return PASS_FLOOR + (PASS_CEILING - PASS_FLOOR) * min(1.0, max(0.0, margin))


def _check(metric: str, value: float, spec: dict) -> tuple[float, Verdict, str]:
    """Score one criterion in [0, 1] with a verdict and a plain-English comment."""
    low, high = spec.get("min"), spec.get("max")
    label = spec.get("label") or METRIC_LABELS.get(metric, (metric, ""))[0]
    unit = METRIC_LABELS.get(metric, ("", ""))[1]
    suffix = f" {unit}" if unit else ""

    if low is not None and value < low:
        # Express the shortfall relative to the window width where there is
        # one, and relative to the bound itself for one-sided criteria.
        scale = (high - low) if high is not None else abs(low)
        deficit = (low - value) / scale if scale > 0 else 1.0
        if deficit <= BORDERLINE_TOLERANCE:
            return (
                BORDERLINE_CEILING
                - (BORDERLINE_CEILING - BORDERLINE_FLOOR) * (deficit / BORDERLINE_TOLERANCE),
                "borderline",
                f"{label} is {value:.3g}{suffix}, just below the {low:g} target.",
            )
        return (
            max(0.0, BORDERLINE_FLOOR - deficit),
            "fail",
            f"{label} is {value:.3g}{suffix}, below the {low:g} minimum.",
        )

    if high is not None and value > high:
        scale = (high - low) if low is not None else abs(high)
        excess = (value - high) / scale if scale > 0 else 1.0
        if excess <= BORDERLINE_TOLERANCE:
            return (
                BORDERLINE_CEILING
                - (BORDERLINE_CEILING - BORDERLINE_FLOOR) * (excess / BORDERLINE_TOLERANCE),
                "borderline",
                f"{label} is {value:.3g}{suffix}, just above the {high:g} target.",
            )
        return (
            max(0.0, BORDERLINE_FLOOR - excess),
            "fail",
            f"{label} is {value:.3g}{suffix}, above the {high:g} maximum.",
        )

    return (
        _pass_score(value, low, high),
        "pass",
        f"{label} is {value:.3g}{suffix}, within the {_describe_target(spec)} target.",
    )


def recommend_applications(
    applications: list[dict],
    classification: Classification,
    raman: RamanResult | None,
    xps: XPSResult | None,
    properties: SampleProperties | None,
    top_n: int = 3,
) -> list[ApplicationMatch]:
    metrics = collect_metrics(raman, xps, properties)
    matches: list[ApplicationMatch] = []

    for app in applications:
        criteria: dict[str, dict] = app.get("criteria", {})
        checks: list[CriterionCheck] = []
        mismatches: list[str] = []
        weighted_sum = 0.0
        total_weight = 0.0
        missing: list[str] = []

        for metric, spec in criteria.items():
            weight = float(spec.get("weight", 1.0))
            label = spec.get("label") or METRIC_LABELS.get(metric, (metric, ""))[0]
            if metric not in metrics:
                missing.append(label)
                checks.append(
                    CriterionCheck(
                        metric=metric,
                        label=label,
                        measured=None,
                        target=_describe_target(spec),
                        verdict="unknown",
                        comment=f"{label} was not measured for this sample.",
                    )
                )
                continue
            value = metrics[metric]
            score, verdict, comment = _check(metric, value, spec)
            weighted_sum += weight * score
            total_weight += weight
            checks.append(
                CriterionCheck(
                    metric=metric,
                    label=label,
                    measured=round(value, 4),
                    target=_describe_target(spec),
                    verdict=verdict,
                    comment=comment,
                )
            )
            if verdict == "fail":
                mismatches.append(comment)

        metric_score = weighted_sum / total_weight if total_weight > 0 else 0.0

        # Form agreement is a separate axis: a sample can hit every numeric
        # target and still be the wrong physical material for the application.
        target_forms = app.get("target_forms") or []
        form_ok = classification.form in target_forms
        form_score = 1.0 if form_ok else 0.25
        if target_forms and not form_ok:
            mismatches.append(
                f"Classified as {classification.label}, but this application calls for "
                + " or ".join(t.replace("_", " ") for t in target_forms)
                + "."
            )

        coverage = total_weight / sum(
            float(s.get("weight", 1.0)) for s in criteria.values()
        ) if criteria else 0.0

        score = 100.0 * metric_score * form_score

        # The verdict comes from the criteria themselves, not from a threshold on
        # the score: the score now grades margin, so a sample that clears every
        # target but sits near an edge would otherwise be mislabelled borderline.
        verdict: Verdict
        evaluated = [c for c in checks if c.verdict != "unknown"]
        if not evaluated:
            verdict = "unknown"
        elif any(c.verdict == "fail" for c in evaluated) or mismatches:
            verdict = "fail"
        elif any(c.verdict == "borderline" for c in evaluated):
            verdict = "borderline"
        else:
            verdict = "pass"

        rationale = _build_rationale(app, classification, checks, mismatches, missing, coverage)

        matches.append(
            ApplicationMatch(
                key=app["key"],
                name=app["name"],
                score=round(score, 1),
                verdict=verdict,
                checks=checks,
                mismatches=mismatches,
                rationale=rationale,
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:top_n] if top_n else matches


def _build_rationale(
    app: dict,
    classification: Classification,
    checks: list[CriterionCheck],
    mismatches: list[str],
    missing: list[str],
    coverage: float,
) -> str:
    passed = [c.label for c in checks if c.verdict == "pass"]
    parts: list[str] = []
    if passed:
        parts.append("Meets the target for " + ", ".join(passed) + ".")
    if mismatches:
        parts.append(mismatches[0])
    if missing:
        parts.append(
            "Not assessed on " + ", ".join(missing) + " (no measurement supplied)."
        )
    if coverage < 0.5 and coverage > 0:
        parts.append(
            "Fewer than half the criteria for this application could be evaluated, so "
            "treat the score as provisional."
        )
    if not parts:
        parts.append(app.get("notes") or "No criteria could be evaluated.")
    return " ".join(parts)
