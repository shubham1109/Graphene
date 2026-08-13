import numpy as np
import pytest
from lmfit.lineshapes import skewed_voigt


# --------------------------------------------------------------------------- #
# Synthetic spectra with known ground truth
# --------------------------------------------------------------------------- #
def lorentzian(x, centre, height, fwhm):
    return height / (1.0 + ((x - centre) / (fwhm / 2.0)) ** 2)


def gaussian_area(x, centre, area, fwhm):
    sigma = fwhm / 2.35482
    return area / (sigma * np.sqrt(2 * np.pi)) * np.exp(-((x - centre) ** 2) / (2 * sigma**2))


@pytest.fixture
def raman_axis():
    return np.linspace(1000.0, 3200.0, 2400)


def make_raman(axis, bands, background=None, noise=3.0, seed=0):
    """bands: list of (centre, height, fwhm). Returns intensity array."""
    rng = np.random.default_rng(seed)
    y = np.zeros_like(axis)
    for centre, height, fwhm in bands:
        y += lorentzian(axis, centre, height, fwhm)
    y = y + (background if background is not None else 80.0 + 0.01 * axis)
    return y + rng.normal(0.0, noise, axis.size)


def make_c1s(components, sp2_area=None, noise=20.0, seed=1, shift=0.0, n=900):
    """components: list of (BE, area, fwhm) for the symmetric lines.

    `sp2_area` adds a properly asymmetric graphitic sp2 line at 284.5 eV, which
    is what real graphene produces; using a symmetric line here would let the
    fitter look better than it deserves.
    """
    rng = np.random.default_rng(seed)
    x = np.linspace(281.0, 293.0, n)
    y = np.zeros_like(x)
    if sp2_area:
        line = skewed_voigt(
            x, amplitude=1.0, center=284.5 + shift, sigma=0.42, gamma=0.12, skew=0.45
        )
        y += sp2_area * line / np.trapezoid(line, x)
    for centre, area, fwhm in components:
        y += gaussian_area(x, centre + shift, area, fwhm)
    y += 300.0 + 40.0 * (x - x[0]) / (x[-1] - x[0])
    return x, y + rng.normal(0.0, noise, x.size)


def make_o1s(components, noise=20.0, seed=2, shift=0.0, n=600):
    rng = np.random.default_rng(seed)
    x = np.linspace(527.0, 540.0, n)
    y = np.zeros_like(x)
    for centre, area, fwhm in components:
        y += gaussian_area(x, centre + shift, area, fwhm)
    y += 250.0 + 30.0 * (x - x[0]) / (x[-1] - x[0])
    return x, y + rng.normal(0.0, noise, x.size)


@pytest.fixture
def monolayer_raman(raman_axis):
    return raman_axis, make_raman(
        raman_axis,
        [(1350.0, 18.0, 40.0), (1582.0, 400.0, 15.0), (2688.0, 1010.0, 30.0)],
        background=100.0 + 0.02 * raman_axis,
    )


@pytest.fixture
def gnp_raman(raman_axis):
    return raman_axis, make_raman(
        raman_axis,
        [
            (1350.0, 280.0, 50.0),
            (1582.0, 1000.0, 24.0),
            (1620.0, 100.0, 24.0),
            (2712.0, 380.0, 76.0),
        ],
        noise=4.0,
        seed=3,
    )


@pytest.fixture
def go_raman(raman_axis):
    return raman_axis, make_raman(
        raman_axis,
        [(1350.0, 1000.0, 160.0), (1595.0, 1020.0, 100.0)],
        noise=4.0,
        seed=5,
    )


@pytest.fixture
def rgo_xps():
    c1s = make_c1s(
        [(285.3, 1200.0, 1.1), (286.6, 900.0, 1.1), (287.9, 300.0, 1.1), (289.0, 250.0, 1.1), (290.8, 300.0, 1.8)],
        sp2_area=6000.0,
    )
    o1s = make_o1s([(531.2, 400.0, 1.6), (532.7, 900.0, 1.6), (533.9, 200.0, 1.6)])
    return c1s, o1s


@pytest.fixture
def go_xps():
    c1s = make_c1s(
        [(285.3, 600.0, 1.2), (286.6, 3400.0, 1.2), (287.9, 700.0, 1.2), (289.0, 500.0, 1.2)],
        sp2_area=3000.0,
    )
    o1s = make_o1s([(531.2, 900.0, 1.7), (532.7, 3200.0, 1.7), (533.9, 600.0, 1.7)])
    return c1s, o1s


def csv_bytes(x, y, header="", delimiter=","):
    lines = [f"{a:.5f}{delimiter}{b:.5f}" for a, b in zip(x, y)]
    return ((header + "\n" if header else "") + "\n".join(lines)).encode("utf-8")
