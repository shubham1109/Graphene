"""Background estimation for Raman (arPLS) and XPS (Shirley)."""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.integrate import cumulative_trapezoid
from scipy.sparse.linalg import spsolve


def arpls(
    y: np.ndarray,
    lam: float = 1e5,
    ratio: float = 1e-3,
    max_iter: int = 50,
) -> np.ndarray:
    """Asymmetrically reweighted penalised least squares baseline.

    Baek, Park, Ahn & Choo, Analyst 140 (2015) 250. The weighting is driven by
    the statistics of the negative residuals, so sharp Raman bands are excluded
    from the fit without any manual anchor points.
    """
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 5:
        return np.full(n, float(np.min(y)))

    # Second-difference operator; H penalises baseline curvature.
    diff = sparse.diags(
        [1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n), format="csr"
    )
    h = lam * (diff.T @ diff)

    w = np.ones(n)
    z = y.copy()
    for _ in range(max_iter):
        wd = sparse.diags(w, 0, format="csr")
        try:
            z = spsolve((wd + h).tocsc(), w * y)
        except RuntimeError:  # pragma: no cover - singular system
            break
        d = y - z
        negatives = d[d < 0]
        if negatives.size == 0:
            break
        mean, std = float(np.mean(negatives)), float(np.std(negatives))
        if std <= 0:
            break
        # Logistic reweighting; clipped because exp() overflows on strong peaks.
        exponent = np.clip(2.0 * (d - (2.0 * std - mean)) / std, -500.0, 500.0)
        w_new = 1.0 / (1.0 + np.exp(exponent))
        denom = float(np.linalg.norm(w))
        if denom > 0 and float(np.linalg.norm(w - w_new)) / denom < ratio:
            w = w_new
            break
        w = w_new
    return np.asarray(z, dtype=float)


def arpls_auto(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float = 600.0,
    **kwargs,
) -> np.ndarray:
    """arPLS with the smoothing parameter derived from the sampling interval.

    A fixed `lam` means the baseline stiffness depends on how densely the
    instrument sampled the spectrum, so the same material analysed at 0.5 and
    2 cm-1/point would get different answers. For a second-difference Whittaker
    smoother the half-power period is about 2*pi*lam**0.25 samples, so solving
    for the `cutoff`-wide feature we want the baseline to follow makes the
    result depend on physical width instead of point count.
    """
    x = np.asarray(x, dtype=float)
    if x.size < 5:
        return np.full(x.size, float(np.min(y)))
    spacing = float(np.median(np.diff(x)))
    if spacing <= 0:
        return arpls(y, **kwargs)
    period_samples = max(8.0, cutoff / spacing)
    lam = (period_samples / (2.0 * np.pi)) ** 4
    lam = float(np.clip(lam, 1e2, 1e12))
    return arpls(y, lam=lam, **kwargs)


def shirley(
    x: np.ndarray,
    y: np.ndarray,
    max_iter: int = 60,
    tol: float = 1e-6,
) -> np.ndarray:
    """Iterative Shirley inelastic background.

    `x` must be ascending binding energy. The background at energy E is
    proportional to the peak area at lower binding energy, since that is the
    photoemission which has undergone inelastic loss.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 5:
        return np.full(x.size, float(np.min(y)))

    # Average a few points at each end so noise does not set the endpoints.
    edge = max(1, min(5, x.size // 20))
    y_low = float(np.mean(y[:edge]))
    y_high = float(np.mean(y[-edge:]))
    step = y_high - y_low

    background = np.zeros_like(y)
    if abs(step) < 1e-12:
        return np.full_like(y, y_low)

    for _ in range(max_iter):
        integrand = y - y_low - background
        cumulative = cumulative_trapezoid(integrand, x, initial=0.0)
        total = float(cumulative[-1])
        if total <= 0:
            # No net peak area above the low-energy endpoint; a Shirley shape is
            # undefined, so fall back to a straight line.
            return np.linspace(y_low, y_high, x.size)
        new_background = step * cumulative / total
        delta = float(np.max(np.abs(new_background - background)))
        background = new_background
        if delta < tol * max(abs(step), 1.0):
            break

    return y_low + background


def linear_background(x: np.ndarray, y: np.ndarray, edge_frac: float = 0.05) -> np.ndarray:
    """Straight line through the mean of each end region."""
    n = x.size
    edge = max(1, int(n * edge_frac))
    x0, y0 = float(np.mean(x[:edge])), float(np.mean(y[:edge]))
    x1, y1 = float(np.mean(x[-edge:])), float(np.mean(y[-edge:]))
    if x1 == x0:
        return np.full_like(y, y0)
    slope = (y1 - y0) / (x1 - x0)
    return y0 + slope * (x - x0)


def r_squared(observed: np.ndarray, modelled: np.ndarray) -> float:
    """Coefficient of determination, clamped to [0, 1]."""
    observed = np.asarray(observed, dtype=float)
    modelled = np.asarray(modelled, dtype=float)
    ss_res = float(np.sum((observed - modelled) ** 2))
    ss_tot = float(np.sum((observed - np.mean(observed)) ** 2))
    if ss_tot <= 0:
        return 0.0
    return float(np.clip(1.0 - ss_res / ss_tot, 0.0, 1.0))
