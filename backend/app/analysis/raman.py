"""Raman peak fitting and graphene metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lmfit.models import LorentzianModel, PseudoVoigtModel
from scipy.ndimage import uniform_filter1d

from app.analysis.baseline import arpls_auto, r_squared
from app.models.analysis import FittedPeak, RamanResult

DEFAULT_EXCITATION_NM = 532.0

FIRST_ORDER = (1050.0, 1800.0)
SECOND_ORDER = (2400.0, 3150.0)

# Cancado et al., Nano Letters 11 (2011) 3190 - defect density.
CANCADO_ND = 1.8e22
# Cancado et al., Appl. Phys. Lett. 88 (2006) 163106 - crystallite size.
CANCADO_LA = 2.4e-10
# Above this I(D)/I(G) the sample is past the Tuinstra-Koenig maximum and the
# relations invert, so the derived numbers stop being trustworthy.
STAGE1_LIMIT = 1.0


@dataclass
class _BandSpec:
    name: str
    lo: float
    hi: float
    nominal: float
    max_fwhm: float
    optional: bool = False
    # When a band sits at or below the noise floor: drop it, or keep it and
    # report the derived ratio as an upper limit. A near-absent D band is the
    # headline result for pristine graphene, so discarding it destroys the
    # very information the user came for.
    reject_below_noise: bool = True


FIRST_ORDER_BANDS = [
    _BandSpec("D", 1290.0, 1420.0, 1350.0, 160.0, reject_below_noise=False),
    _BandSpec("G", 1540.0, 1605.0, 1582.0, 120.0, reject_below_noise=False),
    _BandSpec("D'", 1605.0, 1645.0, 1620.0, 60.0, optional=True),
    _BandSpec("D3", 1440.0, 1535.0, 1500.0, 200.0, optional=True),
]
SECOND_ORDER_BANDS = [
    _BandSpec("2D", 2600.0, 2800.0, 2700.0, 200.0),
    _BandSpec("D+D'", 2880.0, 3010.0, 2940.0, 200.0, optional=True),
]


# A fitted band must clear this many noise sigma to be believed, and be at
# least this wide, before it is reported as a real feature.
MIN_SNR = 4.0
MIN_PHYSICAL_FWHM = 8.0


def _window(x: np.ndarray, y: np.ndarray, lo: float, hi: float):
    mask = (x >= lo) & (x <= hi)
    return x[mask], y[mask]


def estimate_noise(y: np.ndarray) -> float:
    """Robust noise sigma from the second difference.

    Differencing twice annihilates any locally smooth signal and inflates white
    noise variance by a factor of six, so a MAD of the second difference gives
    an outlier-resistant sigma even when strong bands are present.
    """
    if y.size < 5:
        return 0.0
    d2 = np.diff(y, n=2)
    mad = float(np.median(np.abs(d2 - np.median(d2))))
    return 1.4826 * mad / np.sqrt(6.0)


def _seed(x: np.ndarray, y: np.ndarray, spec: _BandSpec) -> tuple[float, float, float] | None:
    """Return (center, height, fwhm_guess) seeded from the data, or None."""
    xw, yw = _window(x, y, spec.lo, spec.hi)
    if xw.size < 5:
        return None
    smooth = uniform_filter1d(yw, size=max(3, xw.size // 40))
    idx = int(np.argmax(smooth))
    center, height = float(xw[idx]), float(smooth[idx])
    if height <= 0:
        return None
    # Half-maximum crossings inside the window give a usable width guess.
    half = height / 2.0
    left = xw[:idx][smooth[:idx] <= half]
    right = xw[idx:][smooth[idx:] <= half]
    lo_edge = float(left[-1]) if left.size else float(xw[0])
    hi_edge = float(right[0]) if right.size else float(xw[-1])
    fwhm = max(6.0, min(spec.max_fwhm, hi_edge - lo_edge))
    return center, height, fwhm


def _prefix(name: str) -> str:
    # lmfit prefixes must be valid Python identifiers, so "2D" cannot lead with
    # a digit and "D+D'" cannot contain punctuation.
    return "b" + name.replace("'", "p").replace("+", "plus").lower() + "_"


def _build_model(specs: list[_BandSpec], seeds: dict[str, tuple[float, float, float]]):
    model = None
    params = None
    for spec in specs:
        if spec.name not in seeds:
            continue
        center, height, fwhm = seeds[spec.name]
        prefix = _prefix(spec.name)
        component = PseudoVoigtModel(prefix=prefix)
        sigma = max(2.0, fwhm / 2.0)
        # PseudoVoigt amplitude is area; approximate it from height and width.
        area = height * sigma * np.pi
        cpars = component.make_params()
        cpars[f"{prefix}center"].set(value=center, min=spec.lo, max=spec.hi)
        cpars[f"{prefix}sigma"].set(value=sigma, min=1.5, max=spec.max_fwhm / 2.0)
        cpars[f"{prefix}amplitude"].set(value=area, min=0.0)
        cpars[f"{prefix}fraction"].set(value=0.7, min=0.0, max=1.0)
        model = component if model is None else model + component
        params = cpars if params is None else params.update(cpars) or params
    return model, params


def _fit_region(
    x: np.ndarray,
    y: np.ndarray,
    specs: list[_BandSpec],
    notes: list[str],
    noise: float,
    region_label: str,
) -> tuple[dict[str, FittedPeak], float | None]:
    """Fit a band group, admitting optional bands only if they earn their place."""
    seeds: dict[str, tuple[float, float, float]] = {}
    for spec in specs:
        seed = _seed(x, y, spec)
        if seed is not None:
            seeds[spec.name] = seed

    required = [s for s in specs if not s.optional and s.name in seeds]
    if not required:
        return {}, None

    def run(active: list[_BandSpec]):
        model, params = _build_model(active, seeds)
        if model is None:
            return None
        try:
            return model.fit(y, params, x=x, nan_policy="omit")
        except Exception as exc:  # pragma: no cover - lmfit surfaces many types
            notes.append(f"Peak fit failed for {[s.name for s in active]}: {exc}")
            return None

    active = [s for s in specs if not s.optional and s.name in seeds]
    best = run(active)
    if best is None:
        return {}, None

    # Add each optional band only when it lowers AIC and is not a sliver.
    for spec in specs:
        if not spec.optional or spec.name not in seeds:
            continue
        trial_specs = active + [spec]
        trial = run(trial_specs)
        if trial is None:
            continue
        prefix = _prefix(spec.name)
        height = trial.params.get(f"{prefix}height")
        strongest = max(
            (
                trial.params[f"{_prefix(s.name)}height"].value
                for s in trial_specs
                if f"{_prefix(s.name)}height" in trial.params
            ),
            default=0.0,
        )
        significant = (
            height is not None and strongest > 0 and height.value / strongest >= 0.02
        )
        if trial.aic < best.aic - 2.0 and significant:
            best, active = trial, trial_specs
        else:
            notes.append(f"{spec.name} band not resolved; excluded from the fit.")

    # Discard bands the data does not actually support - an unconstrained fit
    # will happily place a razor-thin "2D" peak on a patch of noise - and refit
    # with the survivors so the remaining parameters are not skewed by them.
    threshold = MIN_SNR * noise
    below_noise: set[str] = set()
    for _ in range(3):
        rejected = []
        for spec in active:
            prefix = _prefix(spec.name)
            weak = best.params[f"{prefix}height"].value < threshold
            too_narrow = best.params[f"{prefix}fwhm"].value < MIN_PHYSICAL_FWHM
            if too_narrow or (weak and spec.reject_below_noise):
                rejected.append(spec)
            elif weak:
                below_noise.add(spec.name)
        if not rejected:
            break
        for spec in rejected:
            height = best.params[f"{_prefix(spec.name)}height"].value
            notes.append(
                f"{spec.name} band rejected in the {region_label} region: fitted "
                f"height {height:.3g} is below {MIN_SNR:g}x the noise level "
                f"({noise:.3g}) or the band is unphysically narrow."
            )
        active = [spec for spec in active if spec not in rejected]
        if not active:
            return {}, None
        refit = run(active)
        if refit is None:
            return {}, None
        best = refit

    for name in sorted(below_noise):
        notes.append(
            f"{name} band is at or below the noise floor (height < {MIN_SNR:g} sigma). "
            f"Ratios involving {name} should be read as upper limits."
        )

    peaks: dict[str, FittedPeak] = {}
    for spec in active:
        prefix = _prefix(spec.name)
        params = best.params
        centre = params[f"{prefix}center"]
        height = params[f"{prefix}height"]
        peaks[spec.name] = FittedPeak(
            name=spec.name,
            center_cm1=float(centre.value),
            height=float(height.value),
            area=float(params[f"{prefix}amplitude"].value),
            fwhm_cm1=float(params[f"{prefix}fwhm"].value),
            shape=f"pseudo-Voigt (eta={params[f'{prefix}fraction'].value:.2f})",
            center_stderr=float(centre.stderr) if centre.stderr else None,
            height_stderr=float(height.stderr) if height.stderr else None,
        )
    return peaks, r_squared(y, best.best_fit)


def _two_d_shape(
    x: np.ndarray, y: np.ndarray
) -> tuple[bool | None, float | None, float | None]:
    """Compare a single Lorentzian against the 4-component AB-bilayer shape."""
    xw, yw = _window(x, y, 2550.0, 2850.0)
    if xw.size < 30 or float(np.max(yw)) <= 0:
        return None, None, None

    single = LorentzianModel(prefix="l_")
    idx = int(np.argmax(yw))
    pars = single.make_params()
    pars["l_center"].set(value=float(xw[idx]), min=2600.0, max=2800.0)
    pars["l_sigma"].set(value=20.0, min=2.0, max=120.0)
    pars["l_amplitude"].set(value=float(np.trapezoid(yw, xw)), min=0.0)
    try:
        single_fit = single.fit(yw, pars, x=xw)
    except Exception:  # pragma: no cover
        return None, None, None
    r2_single = r_squared(yw, single_fit.best_fit)

    centre = float(single_fit.params["l_center"].value)
    fwhm = float(single_fit.params["l_fwhm"].value)
    model = None
    quad_pars = None
    for i, offset in enumerate((-1.5, -0.5, 0.5, 1.5)):
        prefix = f"q{i}_"
        component = LorentzianModel(prefix=prefix)
        cp = component.make_params()
        guess = centre + offset * fwhm / 3.0
        cp[f"{prefix}center"].set(value=guess, min=guess - 40.0, max=guess + 40.0)
        cp[f"{prefix}sigma"].set(value=max(4.0, fwhm / 5.0), min=2.0, max=60.0)
        cp[f"{prefix}amplitude"].set(
            value=float(np.trapezoid(yw, xw)) / 4.0, min=0.0
        )
        model = component if model is None else model + component
        quad_pars = cp if quad_pars is None else quad_pars.update(cp) or quad_pars
    r2_quad = None
    try:
        quad_fit = model.fit(yw, quad_pars, x=xw)
        r2_quad = r_squared(yw, quad_fit.best_fit)
    except Exception:  # pragma: no cover
        pass

    # The question is not "does a 12-parameter model fit better" - it always
    # does - but "does one Lorentzian leave a systematic residual". Comparing
    # the two sums of squares is robust to residual baseline error, which both
    # models carry equally, in a way an absolute chi-square is not. AB-stacked
    # few-layer graphene leaves a large residual under one Lorentzian; a
    # monolayer or turbostratic 2D band does not.
    if r2_quad is None:
        return (r2_single >= 0.99), r2_single, r2_quad
    sse_single = max(1.0 - r2_single, 1e-12)
    sse_quad = max(1.0 - r2_quad, 1e-12)
    # Empirically the ratio sits near 2-3 for genuinely single Lorentzians
    # (the four-component model just mops up noise) and above 20 for AB-stacked
    # bilayers, so the boundary is not delicate.
    return bool(sse_single / sse_quad < 5.0), r2_single, r2_quad


# Below this the 2D band is vestigial rather than a stacking feature, and no
# layer assignment it supports would be meaningful.
MIN_2D_FOR_LAYERS = 0.25


def _infer_layers(
    fwhm_2d: float | None,
    i2d_ig: float | None,
    single_lorentzian: bool | None,
    excitation_nm: float,
    notes: list[str],
) -> str | None:
    """Layer count from 2D width and I(2D)/I(G).

    Both thresholds are calibrated for 532 nm on SiO2/Si; the 2D band disperses
    by roughly 100 cm-1/eV, and its width shifts with it, so other lasers get a
    caveat rather than a silently wrong answer.
    """
    if fwhm_2d is None and i2d_ig is None:
        return None
    if abs(excitation_nm - DEFAULT_EXCITATION_NM) > 15:
        notes.append(
            f"Layer thresholds are calibrated for 532 nm; this spectrum was taken "
            f"at {excitation_nm:g} nm, so the layer estimate is indicative only."
        )

    # A 2D band this broad is not a stacking-order feature at all - it is the
    # quenched remnant seen in GO and rGO, where the sp2 network is fragmented.
    # Reading a layer count off its width would be meaningless.
    if fwhm_2d is not None and fwhm_2d > 95:
        notes.append(
            f"The 2D band is {fwhm_2d:.0f} cm-1 wide, far broader than any stacking "
            "sequence produces. This is the quenched 2D band of an oxidised or "
            "heavily fragmented sp2 network, so no layer count is reported."
        )
        fwhm_2d = None

    # Width alone is not enough of a guard. Baseline removal narrows a very broad
    # band, so a quenched 2D can still fit under 95 cm-1 while being far too weak
    # to carry a layer assignment. Intensity relative to G is the sharper test.
    if i2d_ig is not None and i2d_ig < MIN_2D_FOR_LAYERS:
        notes.append(
            f"I(2D)/I(G) is only {i2d_ig:.2f}. A 2D band this weak is vestigial - "
            "characteristic of an oxidised or strongly disordered sp2 network - so no "
            "layer count is reported. Use C/O and I(D)/I(G) to judge this material."
        )
        return None

    by_width = None
    if fwhm_2d is not None:
        if fwhm_2d <= 40:
            by_width = "1L"
        elif fwhm_2d <= 55:
            by_width = "2L"
        elif fwhm_2d <= 72:
            by_width = "3-5L"
        else:
            by_width = ">5L"

    by_ratio = None
    if i2d_ig is not None:
        if i2d_ig >= 2.0:
            by_ratio = "1L"
        elif i2d_ig >= 1.0:
            by_ratio = "2L"
        elif i2d_ig >= 0.5:
            by_ratio = "3-5L"
        else:
            by_ratio = ">5L"

    # Band shape beats band width for AB-stacked bilayers: their 2D envelope is
    # as wide as few-layer material, but it resolves into four components while
    # keeping I(2D)/I(G) above 1.
    if (
        single_lorentzian is False
        and i2d_ig is not None
        and i2d_ig >= 1.0
        and by_width in {"2L", "3-5L"}
    ):
        notes.append(
            "The 2D band resolves into multiple components with I(2D)/I(G) above 1 - "
            "the signature of AB (Bernal) stacked bilayer graphene."
        )
        return "2L"

    if by_width and by_ratio and by_width != by_ratio:
        if single_lorentzian and by_width in {"2L", "3-5L"}:
            notes.append(
                f"2D width suggests {by_width} but the band is a single Lorentzian "
                "and I(2D)/I(G) is low - consistent with turbostratic (rotationally "
                "disordered) stacking rather than AB-stacked few-layer."
            )
        else:
            notes.append(
                f"2D width suggests {by_width} while I(2D)/I(G) suggests {by_ratio}; "
                "reporting the width-based estimate, which is the more robust of the two."
            )
    return by_width or by_ratio


def analyse_raman(
    x: np.ndarray,
    y: np.ndarray,
    excitation_nm: float | None = None,
    spectrum_id: str | None = None,
) -> RamanResult:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    notes: list[str] = []
    laser = float(excitation_nm or DEFAULT_EXCITATION_NM)
    if excitation_nm is None:
        notes.append("No excitation wavelength supplied; assumed 532 nm.")

    corrected = y - arpls_auto(x, y)
    noise = estimate_noise(corrected)

    first_x, first_y = _window(x, corrected, *FIRST_ORDER)
    second_x, second_y = _window(x, corrected, *SECOND_ORDER)

    peaks: dict[str, FittedPeak] = {}
    r2_parts: list[tuple[float, int]] = []

    if first_x.size >= 30:
        found, r2 = _fit_region(
            first_x, first_y, FIRST_ORDER_BANDS, notes, noise, "first-order"
        )
        peaks.update(found)
        if r2 is not None:
            r2_parts.append((r2, first_x.size))
    else:
        notes.append(
            "Spectrum does not cover the first-order region (1050-1800 cm-1); "
            "I(D)/I(G) is unavailable."
        )

    if second_x.size >= 30:
        found, r2 = _fit_region(
            second_x, second_y, SECOND_ORDER_BANDS, notes, noise, "second-order"
        )
        peaks.update(found)
        if found and r2 is not None:
            r2_parts.append((r2, second_x.size))
        if not found:
            notes.append(
                "No second-order (2D) band detected above the noise floor. This is "
                "typical of graphene oxide and heavily disordered carbon, where the "
                "2D band is quenched; layer count cannot be assigned."
            )
    else:
        notes.append(
            "Spectrum does not cover the 2D region (2400-3150 cm-1); layer count "
            "cannot be determined from Raman alone."
        )

    d, g, dp, two_d = peaks.get("D"), peaks.get("G"), peaks.get("D'"), peaks.get("2D")

    id_ig = d.height / g.height if d and g and g.height > 0 else None
    id_ig_area = d.area / g.area if d and g and g.area > 0 else None
    i2d_ig = two_d.height / g.height if two_d and g and g.height > 0 else None
    i2d_ig_area = two_d.area / g.area if two_d and g and g.area > 0 else None
    idp_ig = dp.height / g.height if dp and g and g.height > 0 else None

    single_lorentzian, r2_single, r2_quad = (None, None, None)
    if two_d is not None:
        single_lorentzian, r2_single, r2_quad = _two_d_shape(x, corrected)

    la = nd = ld = None
    if id_ig is not None and id_ig > 0:
        if id_ig <= STAGE1_LIMIT:
            la = CANCADO_LA * (laser**4) / id_ig
            nd = (CANCADO_ND / (laser**4)) * id_ig
            # nD is per cm^2; mean spacing in nm.
            ld = float(np.sqrt(1.0 / nd) * 1e7) if nd > 0 else None
        else:
            notes.append(
                f"I(D)/I(G) = {id_ig:.2f} is past the Tuinstra-Koenig maximum, where "
                "the Cancado relations become double-valued. Crystallite size and "
                "defect density are not reported; the material is in the "
                "nanocrystalline/amorphous regime."
            )

    layers = _infer_layers(
        two_d.fwhm_cm1 if two_d else None, i2d_ig, single_lorentzian, laser, notes
    )

    total_points = sum(n for _, n in r2_parts)
    fit_r2 = (
        sum(r2 * n for r2, n in r2_parts) / total_points if total_points else None
    )
    if fit_r2 is not None and fit_r2 < 0.9:
        notes.append(
            f"Overall fit quality is poor (R^2 = {fit_r2:.3f}). Check the baseline "
            "and for cosmic-ray spikes or fluorescence."
        )

    return RamanResult(
        spectrum_id=spectrum_id,
        excitation_nm=laser,
        peaks=sorted(peaks.values(), key=lambda p: p.center_cm1),
        id_ig=id_ig,
        id_ig_area=id_ig_area,
        i2d_ig=i2d_ig,
        i2d_ig_area=i2d_ig_area,
        idprime_ig=idp_ig,
        fwhm_2d_cm1=two_d.fwhm_cm1 if two_d else None,
        fwhm_g_cm1=g.fwhm_cm1 if g else None,
        g_position_cm1=g.center_cm1 if g else None,
        two_d_position_cm1=two_d.center_cm1 if two_d else None,
        crystallite_size_la_nm=la,
        defect_density_cm2=nd,
        mean_defect_distance_nm=ld,
        two_d_single_lorentzian=single_lorentzian,
        two_d_lorentzian_r2=r2_single,
        two_d_four_component_r2=r2_quad,
        estimated_layers=layers,
        fit_r_squared=fit_r2,
        notes=notes,
    )
