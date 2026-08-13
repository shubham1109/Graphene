"""XPS background subtraction, C 1s / O 1s deconvolution and quantification."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lmfit.models import PseudoVoigtModel, SkewedVoigtModel

from app.analysis.baseline import r_squared, shirley
from app.models.analysis import XPSComponent, XPSResult

AL_KALPHA = 1486.6

# Scofield photoionisation cross-sections relative to C 1s = 1.000.
SCOFIELD = {"C1s": 1.000, "O1s": 2.930}
# Combined analyser-transmission and inelastic-mean-free-path scaling with
# kinetic energy. 0.6 is the usual compromise exponent for modern hemispherical
# analysers operating in constant-pass-energy mode.
KE_EXPONENT = 0.6

C1S_WINDOW = (281.0, 293.0)
O1S_WINDOW = (527.0, 540.0)

# Reference binding energy for the sp2 C=C line, used for charge referencing.
SP2_REFERENCE = 284.5

# Fixed asymmetry of the graphitic sp2 line, equivalent to a Doniach-Sunjic
# alpha near 0.1 as measured on HOPG. These are held constant rather than
# fitted: an sp2 tail free to grow is almost perfectly degenerate with an sp3
# component 0.7 eV away, and letting both float makes the split arbitrary.
SP2_GAMMA = 0.12
SP2_SKEW = 0.45


@dataclass
class _Component:
    name: str
    assignment: str
    lo: float
    hi: float
    nominal: float
    # Oxygen atoms implied per carbon atom in this environment, used only when
    # no O 1s data is available.
    oxygen_per_carbon: float = 0.0
    asymmetric: bool = False
    optional: bool = True


C1S_COMPONENTS = [
    _Component("sp2", "sp2 C=C (aromatic)", 283.9, 284.9, 284.5, 0.0, asymmetric=True, optional=False),
    _Component("sp3", "sp3 C-C / C-H", 284.9, 285.7, 285.2, 0.0),
    _Component("C-O", "C-O (hydroxyl / epoxy)", 285.9, 287.2, 286.6, 1.0),
    _Component("C=O", "C=O (carbonyl / quinone)", 287.2, 288.4, 287.9, 1.0),
    _Component("O-C=O", "O-C=O (carboxyl / ester)", 288.4, 289.8, 289.0, 2.0),
    _Component("pi-pi*", "pi-pi* shake-up satellite", 289.8, 292.0, 290.8, 0.0),
]

O1S_COMPONENTS = [
    _Component("O=C", "O=C (carbonyl / quinone)", 530.2, 531.9, 531.2, 0.0, optional=False),
    _Component("O-C", "O-C (hydroxyl / epoxy / ester)", 531.9, 533.4, 532.7, 0.0),
    _Component("O-C=O/H2O", "O-C=O, chemisorbed water", 533.4, 535.2, 533.9, 0.0),
]

MIN_SNR = 3.0


def _window(x: np.ndarray, y: np.ndarray, lo: float, hi: float):
    mask = (x >= lo) & (x <= hi)
    return x[mask], y[mask]


def _noise(y: np.ndarray) -> float:
    if y.size < 5:
        return 0.0
    d2 = np.diff(y, n=2)
    mad = float(np.median(np.abs(d2 - np.median(d2))))
    return 1.4826 * mad / np.sqrt(6.0)


def integrate_region(
    x: np.ndarray, y: np.ndarray, lo: float, hi: float
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray] | None:
    """Shirley-subtract a region and return (area, x, y_corrected, background)."""
    xw, yw = _window(x, y, lo, hi)
    if xw.size < 10:
        return None
    background = shirley(xw, yw)
    corrected = yw - background
    area = float(np.trapezoid(np.clip(corrected, 0.0, None), xw))
    return area, xw, corrected, background


def _carbon_line_position(x: np.ndarray, y: np.ndarray) -> float:
    """Binding energy of the C-C / C=C line within a C 1s region.

    Anchoring on the tallest feature is wrong for heavily oxidised material,
    where the C-O component can outgrow the graphitic line and would drag the
    whole energy scale about two electronvolts off. Every carbon environment
    other than C-C/C=C is chemically shifted to *higher* binding energy, so the
    lowest-energy resolved maximum is the correct anchor.
    """
    if y.size < 5:
        return float(x[int(np.argmax(y))]) if y.size else SP2_REFERENCE
    smooth = np.convolve(y, np.ones(5) / 5.0, mode="same")
    peak_max = float(np.max(smooth))
    if peak_max <= 0:
        return float(x[int(np.argmax(y))])
    from scipy.signal import find_peaks  # noqa: PLC0415 - local to keep import cost down

    indices, _ = find_peaks(smooth, prominence=0.05 * peak_max, height=0.15 * peak_max)
    if indices.size:
        return float(x[int(np.min(indices))])
    return float(x[int(np.argmax(smooth))])


def charge_reference(
    x: np.ndarray, y: np.ndarray, notes: list[str]
) -> tuple[np.ndarray, float]:
    """Shift the C 1s region so the main carbon line sits at 284.5 eV.

    Insulating or partially oxidised samples charge under X-ray irradiation and
    the whole spectrum drifts, often by more than an electronvolt. Without this
    correction every component would be assigned to the wrong chemistry.
    """
    xw, yw = _window(x, y, *C1S_WINDOW)
    if xw.size < 10:
        return x, 0.0
    corrected = yw - shirley(xw, yw)
    peak_be = _carbon_line_position(xw, corrected)
    shift = SP2_REFERENCE - peak_be
    if abs(shift) < 0.05:
        return x, 0.0
    if abs(shift) > 5.0:
        notes.append(
            f"The strongest C 1s feature is at {peak_be:.2f} eV, more than 5 eV from "
            "the expected 284.5 eV. Charge referencing was NOT applied - check the "
            "energy scale calibration of this file."
        )
        return x, 0.0
    notes.append(
        f"Charge-referenced the energy scale by {shift:+.2f} eV to place the main "
        f"C 1s line at {SP2_REFERENCE:.1f} eV (it was measured at {peak_be:.2f} eV)."
    )
    return x + shift, shift


def _fit_components(
    x: np.ndarray,
    y: np.ndarray,
    specs: list[_Component],
    notes: list[str],
    region_label: str,
) -> tuple[list[XPSComponent], float | None]:
    """Fit a region with a shared-width component set."""
    if x.size < 15 or float(np.max(y)) <= 0:
        return [], None

    noise = _noise(y)
    peak_height = float(np.max(y))
    span = float(x[-1] - x[0])

    model = None
    params = None
    used: list[_Component] = []
    for spec in specs:
        if spec.hi < x[0] or spec.lo > x[-1]:
            continue
        prefix = _safe_prefix(spec.name)
        component = (
            SkewedVoigtModel(prefix=prefix) if spec.asymmetric else PseudoVoigtModel(prefix=prefix)
        )
        cpars = component.make_params()
        cpars[f"{prefix}center"].set(value=spec.nominal, min=spec.lo, max=spec.hi)
        cpars[f"{prefix}sigma"].set(value=0.55, min=0.15, max=1.4)
        cpars[f"{prefix}amplitude"].set(value=peak_height * 0.5, min=0.0)
        if spec.asymmetric:
            # Positive skew puts the tail on the high-binding-energy side, the
            # direction conduction-electron screening produces. Held fixed; see
            # SP2_GAMMA / SP2_SKEW.
            cpars[f"{prefix}gamma"].set(value=SP2_GAMMA, vary=False)
            cpars[f"{prefix}skew"].set(value=SP2_SKEW, vary=False)
        else:
            cpars[f"{prefix}fraction"].set(value=0.2, min=0.0, max=1.0)
        model = component if model is None else model + component
        params = cpars if params is None else params.update(cpars) or params
        used.append(spec)

    if model is None or params is None or not used:
        return [], None

    # Chemically shifted components of the same element share a line width;
    # letting each float independently makes the decomposition unstable.
    symmetric = [s for s in used if not s.asymmetric]
    if len(symmetric) > 1:
        anchor = _safe_prefix(symmetric[0].name)
        for spec in symmetric[1:]:
            params[f"{_safe_prefix(spec.name)}sigma"].set(expr=f"{anchor}sigma")

    try:
        result = model.fit(y, params, x=x, nan_policy="omit")
    except Exception as exc:  # pragma: no cover
        notes.append(f"{region_label} fit failed: {exc}")
        return [], None

    # Measure each component from its evaluated curve rather than from model
    # parameters: SkewedVoigtModel exposes no derived height or FWHM, and its
    # `amplitude` is the area of the underlying symmetric Voigt, which the skew
    # factor then distorts. Numerical integration is exact for every shape.
    evaluated = result.eval_components(x=x)
    components: list[XPSComponent] = []
    threshold = MIN_SNR * noise
    for spec in used:
        prefix = _safe_prefix(spec.name)
        curve = np.asarray(evaluated[prefix], dtype=float)
        height, fwhm = _component_stats(x, curve)
        area = float(np.trapezoid(np.clip(curve, 0.0, None), x))
        if area <= 0 or (height < threshold and spec.optional):
            continue
        if fwhm > span:
            continue
        components.append(
            XPSComponent(
                name=spec.name,
                assignment=spec.assignment,
                # Report the mode, not the `center` parameter: for the
                # asymmetric sp2 line the skew displaces the maximum from the
                # nominal centre, and the mode is what an analyst reads off.
                binding_energy_ev=float(x[int(np.argmax(curve))]),
                area=area,
                fwhm_ev=fwhm,
                fraction_of_region=0.0,
            )
        )

    total = sum(c.area for c in components)
    if total <= 0:
        return [], None
    for component in components:
        component.fraction_of_region = component.area / total

    dropped = len(used) - len(components)
    if dropped:
        notes.append(
            f"{dropped} {region_label} component(s) were not resolved above the noise "
            "floor and were excluded."
        )
    return sorted(components, key=lambda c: c.binding_energy_ev), r_squared(y, result.best_fit)


def _component_stats(x: np.ndarray, curve: np.ndarray) -> tuple[float, float]:
    """Peak height and numerically measured FWHM of one fitted component."""
    if curve.size == 0:
        return 0.0, 0.0
    idx = int(np.argmax(curve))
    height = float(curve[idx])
    if height <= 0:
        return 0.0, 0.0
    half = height / 2.0
    left = np.where(curve[:idx] <= half)[0]
    right = np.where(curve[idx:] <= half)[0]
    lo = float(x[left[-1]]) if left.size else float(x[0])
    hi = float(x[idx + right[0]]) if right.size else float(x[-1])
    return height, abs(hi - lo)


def _safe_prefix(name: str) -> str:
    cleaned = (
        name.replace("-", "_")
        .replace("=", "eq")
        .replace("*", "star")
        .replace("/", "_")
        .replace("+", "plus")
    )
    return f"c{cleaned}_"


def _atomic_ratio(area_c: float, area_o: float, photon_energy: float) -> float | None:
    """Scofield-corrected C/O atomic ratio."""
    if area_o <= 0 or area_c <= 0:
        return None
    ke_c = max(photon_energy - 285.0, 1.0)
    ke_o = max(photon_energy - 532.0, 1.0)
    n_c = area_c / (SCOFIELD["C1s"] * ke_c**KE_EXPONENT)
    n_o = area_o / (SCOFIELD["O1s"] * ke_o**KE_EXPONENT)
    return n_c / n_o if n_o > 0 else None


def analyse_xps(
    regions: dict[str, tuple[np.ndarray, np.ndarray]],
    photon_energy: float = AL_KALPHA,
    spectrum_ids: list[str] | None = None,
) -> XPSResult:
    """Analyse whichever of survey / C 1s / O 1s regions are available."""
    notes: list[str] = []
    analysed: list[str] = []

    c1s_x = c1s_y = None
    o1s_x = o1s_y = None
    area_c = area_o = None

    if "c1s" in regions:
        raw_x, raw_y = regions["c1s"]
        shifted_x, _ = charge_reference(raw_x, raw_y, notes)
        result = integrate_region(shifted_x, raw_y, *C1S_WINDOW)
        if result:
            area_c, c1s_x, c1s_y, _ = result
            analysed.append("c1s")

    if "o1s" in regions:
        raw_x, raw_y = regions["o1s"]
        result = integrate_region(raw_x, raw_y, *O1S_WINDOW)
        if result:
            area_o, o1s_x, o1s_y, _ = result
            analysed.append("o1s")

    survey_ratio = None
    if "survey" in regions:
        sx, sy = regions["survey"]
        survey_c = integrate_region(sx, sy, *C1S_WINDOW)
        survey_o = integrate_region(sx, sy, *O1S_WINDOW)
        if survey_c and survey_o:
            survey_ratio = _atomic_ratio(survey_c[0], survey_o[0], photon_energy)
            analysed.append("survey")
            if c1s_x is None:
                # Fall back to the survey scan for peak fitting, with a caveat:
                # survey energy resolution is far too coarse for a trustworthy
                # chemical-state decomposition.
                shifted_x, _ = charge_reference(sx, sy, notes)
                result = integrate_region(shifted_x, sy, *C1S_WINDOW)
                if result:
                    _, c1s_x, c1s_y, _ = result
                    notes.append(
                        "No high-resolution C 1s scan was supplied, so the C 1s "
                        "envelope was deconvolved from the survey scan. Component "
                        "fractions from a survey are indicative only - acquire a "
                        "high-resolution C 1s region for quantitative chemistry."
                    )
        elif survey_c and not survey_o:
            analysed.append("survey")
            notes.append(
                "No O 1s signal was detected in the survey scan; the sample appears "
                "to carry very little oxygen."
            )

    c1s_components: list[XPSComponent] = []
    o1s_components: list[XPSComponent] = []
    c1s_r2 = o1s_r2 = None

    if c1s_x is not None and c1s_y is not None:
        c1s_components, c1s_r2 = _fit_components(
            c1s_x, c1s_y, C1S_COMPONENTS, notes, "C 1s"
        )
    if o1s_x is not None and o1s_y is not None:
        o1s_components, o1s_r2 = _fit_components(
            o1s_x, o1s_y, O1S_COMPONENTS, notes, "O 1s"
        )

    # --- C/O ratio, preferring the most reliable available source ----------- #
    co_ratio: float | None = None
    co_source: str | None = None
    if area_c is not None and area_o is not None:
        co_ratio = _atomic_ratio(area_c, area_o, photon_energy)
        co_source = "high-resolution C 1s and O 1s regions (Scofield corrected)"
    elif survey_ratio is not None:
        co_ratio = survey_ratio
        co_source = "survey scan (Scofield corrected)"
    elif c1s_components:
        co_ratio, estimate_note = _estimate_co_from_c1s(c1s_components)
        co_source = "estimated from C 1s chemical shifts (no O 1s data)"
        notes.append(estimate_note)

    carbon_pct = oxygen_pct = None
    if co_ratio is not None and co_ratio > 0:
        # Two-element normalisation; any other element present is unaccounted for.
        carbon_pct = 100.0 * co_ratio / (1.0 + co_ratio)
        oxygen_pct = 100.0 - carbon_pct

    # --- sp2 / sp3 ---------------------------------------------------------- #
    sp2 = next((c for c in c1s_components if c.name == "sp2"), None)
    sp3 = next((c for c in c1s_components if c.name == "sp3"), None)
    shakeup = next((c for c in c1s_components if c.name == "pi-pi*"), None)
    # The shake-up satellite is intensity stolen from the main line, not a
    # distinct carbon environment, so it is excluded from the denominator.
    primary_total = sum(c.area for c in c1s_components if c.name != "pi-pi*")

    sp2_fraction = sp2.area / primary_total if sp2 and primary_total > 0 else None
    sp3_fraction = sp3.area / primary_total if sp3 and primary_total > 0 else None
    sp2_sp3 = (
        sp2.area / sp3.area if sp2 and sp3 and sp3.area > 0 else None
    )
    if sp2 and sp3:
        notes.append(
            "The sp2 asymmetric tail and the sp3 component overlap strongly, so the "
            f"sp2/sp3 split depends on the assumed sp2 lineshape (fixed here at "
            f"gamma={SP2_GAMMA}, skew={SP2_SKEW}, calibrated on HOPG). Treat the split "
            "as accurate to roughly +/-10 percentage points; the total oxygenated "
            "carbon fraction and C/O ratio are far better constrained."
        )
    if sp2 and not sp3:
        notes.append(
            "No distinct sp3 component was resolved; the carbon line is essentially "
            "fully sp2, so the sp2/sp3 ratio is reported as unbounded."
        )

    functional_groups = {
        c.name: c.fraction_of_region for c in c1s_components if c.name not in {"sp2", "sp3"}
    }

    if shakeup is not None:
        notes.append(
            "A pi-pi* shake-up satellite is present, confirming a delocalised "
            "aromatic system - characteristic of graphitic rather than amorphous carbon."
        )

    for label, r2 in (("C 1s", c1s_r2), ("O 1s", o1s_r2)):
        if r2 is not None and r2 < 0.95:
            notes.append(
                f"{label} fit quality is marginal (R^2 = {r2:.3f}); treat the component "
                "fractions with caution."
            )

    if not analysed:
        notes.append("No recognisable XPS region was found in the supplied data.")

    return XPSResult(
        spectrum_ids=spectrum_ids or [],
        regions_analysed=analysed,
        c1s_components=c1s_components,
        o1s_components=o1s_components,
        co_ratio=co_ratio,
        co_ratio_source=co_source,
        carbon_at_pct=carbon_pct,
        oxygen_at_pct=oxygen_pct,
        sp2_fraction=sp2_fraction,
        sp3_fraction=sp3_fraction,
        sp2_sp3_ratio=sp2_sp3,
        functional_groups=functional_groups,
        pi_pi_star_present=shakeup is not None if c1s_components else None,
        c1s_fit_r_squared=c1s_r2,
        o1s_fit_r_squared=o1s_r2,
        notes=notes,
    )


def _estimate_co_from_c1s(components: list[XPSComponent]) -> tuple[float | None, str]:
    """Infer C/O from oxygenated-carbon fractions when no O 1s scan exists."""
    lookup = {spec.name: spec.oxygen_per_carbon for spec in C1S_COMPONENTS}
    total = sum(c.area for c in components if c.name != "pi-pi*")
    if total <= 0:
        return None, "Could not estimate C/O: no carbon intensity."
    oxygen_equiv = sum(
        c.area * lookup.get(c.name, 0.0) for c in components if c.name != "pi-pi*"
    )
    note = (
        "C/O was estimated from C 1s chemical shifts alone by counting the oxygen "
        "implied by each carbon environment. This assumes hydroxyl rather than "
        "epoxy for the C-O component (epoxy oxygen bridges two carbons, which "
        "would roughly halve its contribution) and ignores oxygen bound to "
        "anything other than carbon. Upload an O 1s or survey scan for a "
        "quantitative ratio."
    )
    if oxygen_equiv <= 0:
        return None, note + " No oxygenated carbon was resolved, so C/O is unbounded."
    return total / oxygen_equiv, note
