"""Engine tests against synthetic spectra with known ground truth."""

import json
import re
from pathlib import Path

import numpy as np
import pytest

from app.analysis.baseline import arpls_auto, linear_background, r_squared, shirley
from app.analysis.classify import classify, trapezoid
from app.analysis.applications import recommend_applications
from app.analysis.peers import match_peers
from app.analysis.pipeline import build_report_payload, build_rankings, build_scorecard
from app.analysis.raman import analyse_raman, estimate_noise
from app.analysis.xps import analyse_xps, charge_reference
from app.models.sample import SampleProperties
from tests.conftest import gaussian_area, lorentzian, make_c1s, make_o1s, make_raman

DATA = Path(__file__).resolve().parents[1] / "seed" / "data"


def _gnp_arrays():
    """The GNP fixture as plain arrays, for tests outside a fixture scope."""
    axis = np.linspace(1000.0, 3200.0, 2400)
    return axis, make_raman(
        axis,
        [
            (1350.0, 280.0, 50.0),
            (1582.0, 1000.0, 24.0),
            (1620.0, 100.0, 24.0),
            (2712.0, 380.0, 76.0),
        ],
        noise=4.0,
        seed=3,
    )


@pytest.fixture(scope="module")
def library():
    def load(name):
        with (DATA / name).open() as handle:
            return json.load(handle)

    return {
        "spectra": load("reference_spectra.json"),
        "properties": load("reference_properties.json"),
        "applications": load("applications.json"),
        "products": load("commercial_products.json"),
    }


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #
class TestBaselines:
    def test_arpls_recovers_linear_background(self):
        x = np.linspace(1000.0, 3200.0, 2400)
        truth = 100.0 + 0.02 * x
        y = lorentzian(x, 1582.0, 400.0, 16.0) + lorentzian(x, 2690.0, 1000.0, 30.0) + truth
        estimated = arpls_auto(x, y)
        # Within a few percent of the peak height across the whole range.
        assert np.max(np.abs(estimated - truth)) < 0.02 * 1000.0

    def test_arpls_is_resolution_independent(self):
        """The same material sampled at two densities must give the same answer."""
        results = []
        for n in (1200, 4800):
            x = np.linspace(1000.0, 3200.0, n)
            y = (
                lorentzian(x, 1350.0, 300.0, 50.0)
                + lorentzian(x, 1582.0, 1000.0, 24.0)
                + 100.0
                + 0.02 * x
            )
            result = analyse_raman(x, y, 532.0)
            results.append(result.id_ig)
        assert results[0] == pytest.approx(results[1], rel=0.05)

    def test_shirley_endpoints_match_data(self):
        # A Gaussian core, not a Lorentzian: over a 15 eV window a Lorentzian's
        # tail is still substantial at both edges, so the endpoints would not be
        # background and the measured step would come out negative - which is a
        # property of the test data, not of the algorithm.
        x = np.linspace(280.0, 295.0, 600)
        y = gaussian_area(x, 284.5, 6000.0, 1.2) + 300.0 + 40.0 * (x - 280.0) / 15.0
        background = shirley(x, y)

        assert background[0] == pytest.approx(y[:5].mean(), rel=0.02)
        assert background[-1] == pytest.approx(y[-5:].mean(), rel=0.02)
        assert background[-1] > background[0]

        # Rises monotonically towards high binding energy. The tolerance is tied
        # to the step because in the flat pre-peak region the integrand is zero
        # to within floating-point noise, which wobbles the cumulative integral.
        step = float(y[-5:].mean() - y[:5].mean())
        assert np.all(np.diff(background) >= -1e-3 * abs(step))

        # The defining property: the rise is localised to the peak. Below the
        # peak almost no intensity has been integrated yet, and above it almost
        # all of it has. (Shirley does not guarantee background <= signal for
        # arbitrary input - it models a step, so on data carrying an additional
        # linear ramp the background legitimately overtakes the signal before
        # the ramp catches up.)
        below = float(np.interp(282.0, x, background))
        above = float(np.interp(287.0, x, background))
        assert below == pytest.approx(background[0], abs=0.02 * abs(step))
        assert above > background[0] + 0.9 * step

    def test_shirley_falls_back_when_no_peak(self):
        x = np.linspace(281.0, 293.0, 300)
        y = np.linspace(300.0, 340.0, 300)
        background = shirley(x, y)
        np.testing.assert_allclose(background, y, atol=1.0)

    def test_linear_background(self):
        x = np.linspace(0.0, 10.0, 200)
        y = 3.0 * x + 5.0
        np.testing.assert_allclose(linear_background(x, y), y, atol=0.3)

    def test_r_squared_bounds(self):
        y = np.array([1.0, 2.0, 3.0, 4.0])
        assert r_squared(y, y) == 1.0
        assert r_squared(y, np.zeros_like(y)) == 0.0

    def test_noise_estimate(self):
        rng = np.random.default_rng(0)
        x = np.linspace(0.0, 100.0, 4000)
        signal = 50.0 * np.exp(-((x - 50.0) ** 2) / 20.0)
        y = signal + rng.normal(0.0, 2.5, x.size)
        assert estimate_noise(y) == pytest.approx(2.5, rel=0.15)


# --------------------------------------------------------------------------- #
# Raman
# --------------------------------------------------------------------------- #
class TestRamanEngine:
    def test_monolayer(self, monolayer_raman):
        x, y = monolayer_raman
        result = analyse_raman(x, y, 532.0)
        assert result.estimated_layers == "1L"
        assert result.i2d_ig > 2.0
        assert result.fwhm_2d_cm1 == pytest.approx(30.0, abs=4.0)
        assert result.id_ig < 0.1
        assert result.two_d_single_lorentzian is True
        assert result.fit_r_squared > 0.98

    def test_monolayer_peak_positions(self, monolayer_raman):
        x, y = monolayer_raman
        result = analyse_raman(x, y, 532.0)
        positions = {p.name: p.center_cm1 for p in result.peaks}
        assert positions["G"] == pytest.approx(1582.0, abs=2.0)
        assert positions["2D"] == pytest.approx(2688.0, abs=3.0)

    def test_gnp_few_layer(self, gnp_raman):
        x, y = gnp_raman
        result = analyse_raman(x, y, 532.0)
        assert result.id_ig == pytest.approx(0.28, rel=0.25)
        assert result.estimated_layers in {"3-5L", ">5L"}
        assert result.idprime_ig is not None  # D' resolved

    def test_go_has_no_2d_band(self, go_raman):
        x, y = go_raman
        result = analyse_raman(x, y, 532.0)
        assert result.fwhm_2d_cm1 is None
        assert result.i2d_ig is None
        assert result.estimated_layers is None
        assert result.id_ig == pytest.approx(0.98, rel=0.15)
        assert any("2D" in note for note in result.notes)

    def test_ab_bilayer_identified_by_band_shape(self, raman_axis):
        ab = sum(
            lorentzian(raman_axis, centre, height, 26.0)
            for centre, height in ((2660.0, 180.0), (2685.0, 420.0), (2700.0, 380.0), (2720.0, 150.0))
        )
        y = make_raman(raman_axis, [(1350.0, 25.0, 40.0), (1582.0, 400.0, 17.0)], seed=11) + ab
        result = analyse_raman(raman_axis, y, 532.0)
        assert result.two_d_single_lorentzian is False
        assert result.estimated_layers == "2L"

    def test_turbostratic_single_lorentzian(self, raman_axis):
        y = make_raman(
            raman_axis,
            [(1350.0, 60.0, 40.0), (1582.0, 500.0, 22.0), (2700.0, 450.0, 58.0)],
            seed=12,
        )
        result = analyse_raman(raman_axis, y, 532.0)
        assert result.two_d_single_lorentzian is True

    def test_cancado_defect_metrics(self, gnp_raman):
        x, y = gnp_raman
        result = analyse_raman(x, y, 532.0)
        expected_la = 2.4e-10 * 532.0**4 / result.id_ig
        assert result.crystallite_size_la_nm == pytest.approx(expected_la, rel=1e-6)
        expected_nd = (1.8e22 / 532.0**4) * result.id_ig
        assert result.defect_density_cm2 == pytest.approx(expected_nd, rel=1e-6)

    def test_cancado_suppressed_past_tuinstra_koenig_maximum(self, raman_axis):
        y = make_raman(
            raman_axis, [(1350.0, 1600.0, 90.0), (1600.0, 1000.0, 70.0)], seed=13, noise=4.0
        )
        result = analyse_raman(raman_axis, y, 532.0)
        assert result.id_ig > 1.0
        assert result.crystallite_size_la_nm is None
        assert any("Tuinstra-Koenig" in note for note in result.notes)

    def test_excitation_scaling_of_defect_metrics(self, gnp_raman):
        x, y = gnp_raman
        green = analyse_raman(x, y, 532.0)
        red = analyse_raman(x, y, 633.0)
        # La scales as lambda^4, so a red laser must report a larger domain.
        assert red.crystallite_size_la_nm > green.crystallite_size_la_nm
        assert any("532 nm" in note for note in red.notes)

    def test_weak_d_band_reported_as_upper_limit(self, raman_axis):
        """A D band buried in noise must still yield a flagged I(D)/I(G).

        Dropping it would destroy the headline result for pristine material, so
        the value is kept and labelled an upper limit.
        """
        y = make_raman(
            raman_axis,
            # Fitted D height lands below 4 sigma but the band stays physically
            # wide, which is the regime the upper-limit flag exists for.
            [(1350.0, 10.0, 45.0), (1582.0, 500.0, 15.0), (2688.0, 1100.0, 30.0)],
            noise=3.0,
            seed=31,
        )
        result = analyse_raman(raman_axis, y, 532.0)
        assert result.id_ig is not None
        assert result.id_ig < 0.1
        assert any("upper limit" in note for note in result.notes)

    def test_truncated_spectrum_degrades_gracefully(self):
        x = np.linspace(1100.0, 1800.0, 800)
        y = make_raman(x, [(1350.0, 300.0, 50.0), (1582.0, 1000.0, 24.0)], seed=14)
        result = analyse_raman(x, y, 532.0)
        assert result.id_ig is not None
        assert result.i2d_ig is None
        assert any("2D region" in note for note in result.notes)

    def test_quenched_broad_2d_gives_no_layer_count(self, raman_axis):
        y = make_raman(
            raman_axis,
            [(1345.0, 1100.0, 130.0), (1590.0, 1000.0, 88.0), (2690.0, 160.0, 200.0)],
            seed=15,
            noise=4.0,
        )
        result = analyse_raman(raman_axis, y, 532.0)
        assert result.estimated_layers is None
        assert any("quenched" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# XPS
# --------------------------------------------------------------------------- #
class TestXPSEngine:
    def test_co_ratio_accuracy(self, rgo_xps):
        (cx, cy), (ox, oy) = rgo_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        # Scofield-corrected expectation for the injected areas.
        area_c, area_o = 6000.0 + 1200.0 + 900.0 + 300.0 + 250.0 + 300.0, 1500.0
        expected = (area_c / (1.0 * (1486.6 - 285.0) ** 0.6)) / (
            area_o / (2.93 * (1486.6 - 532.0) ** 0.6)
        )
        assert result.co_ratio == pytest.approx(expected, rel=0.15)
        assert "high-resolution" in result.co_ratio_source

    def test_component_binding_energies(self, rgo_xps):
        (cx, cy), (ox, oy) = rgo_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        positions = {c.name: c.binding_energy_ev for c in result.c1s_components}
        assert positions["sp2"] == pytest.approx(284.5, abs=0.2)
        assert positions["C-O"] == pytest.approx(286.6, abs=0.3)
        assert positions["O-C=O"] == pytest.approx(289.0, abs=0.4)

    def test_shakeup_detected(self, rgo_xps):
        (cx, cy), (ox, oy) = rgo_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        assert result.pi_pi_star_present is True
        assert any("shake-up" in note for note in result.notes)

    def test_fractions_sum_to_one(self, rgo_xps):
        (cx, cy), (ox, oy) = rgo_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        assert sum(c.fraction_of_region for c in result.c1s_components) == pytest.approx(1.0)
        assert sum(c.fraction_of_region for c in result.o1s_components) == pytest.approx(1.0)

    def test_go_is_oxygen_rich(self, go_xps):
        (cx, cy), (ox, oy) = go_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        assert 1.5 < result.co_ratio < 6.0
        assert result.sp2_fraction < 0.55
        assert result.functional_groups["C-O"] > 0.3

    def test_charge_referencing_corrects_shift(self):
        components = [
            (285.3, 600.0, 1.2),
            (286.6, 3400.0, 1.2),
            (287.9, 700.0, 1.2),
            (289.0, 500.0, 1.2),
        ]
        cx, cy = make_c1s(components, sp2_area=3000.0, shift=2.3)
        _shifted, offset = charge_reference(cx, cy, [])

        # Referencing places the sp2 *maximum* at 284.5 eV, which is the position
        # the literature quotes. The asymmetric lineshape puts its maximum above
        # its nominal centre, so the expected offset is the applied shift plus
        # that skew displacement - measured here from the bare lineshape.
        from lmfit.lineshapes import skewed_voigt

        grid = np.linspace(282.0, 288.0, 20000)
        bare = skewed_voigt(grid, amplitude=1.0, center=284.5, sigma=0.42, gamma=0.12, skew=0.45)
        skew_offset = float(grid[np.argmax(bare)]) - 284.5
        assert skew_offset == pytest.approx(0.164, abs=0.02)

        assert offset == pytest.approx(-(2.3 + skew_offset), abs=0.15)

    def test_charge_reference_anchors_lowest_be_line_not_tallest(self):
        """In GO the C-O line can outgrow sp2; the anchor must still be sp2."""
        cx, cy = make_c1s(
            [(286.6, 6000.0, 1.2)],   # C-O far taller than sp2
            sp2_area=2500.0,
        )
        _shifted, offset = charge_reference(cx, cy, [])
        assert abs(offset) < 0.3

    def test_wild_energy_scale_not_silently_shifted(self):
        cx, cy = make_c1s([(285.3, 500.0, 1.2)], sp2_area=4000.0, shift=8.0)
        notes: list[str] = []
        _shifted, offset = charge_reference(cx, cy, notes)
        assert offset == 0.0
        assert any("calibration" in note for note in notes)

    def test_c1s_only_estimate_is_flagged(self, rgo_xps):
        (cx, cy), _ = rgo_xps
        result = analyse_xps({"c1s": (cx, cy)})
        assert result.co_ratio is not None
        assert "estimated" in result.co_ratio_source
        assert any("epoxy" in note for note in result.notes)

    def test_sp2_sp3_uncertainty_is_disclosed(self, rgo_xps):
        (cx, cy), (ox, oy) = rgo_xps
        result = analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        assert any("percentage points" in note for note in result.notes)

    def test_survey_only(self):
        rng = np.random.default_rng(3)
        x = np.linspace(0.0, 1100.0, 4000)
        from tests.conftest import gaussian_area

        y = (
            gaussian_area(x, 284.5, 60000.0, 3.0)
            + gaussian_area(x, 532.0, 12000.0, 3.5)
            + 500.0
            + 0.5 * x
            + rng.normal(0.0, 30.0, x.size)
        )
        result = analyse_xps({"survey": (x, y)})
        assert "survey" in result.regions_analysed
        assert result.co_ratio is not None
        assert any("survey" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
class TestClassification:
    def test_trapezoid_membership(self):
        assert trapezoid(5.0, 0.0, 2.0, 8.0, 10.0) == 1.0
        assert trapezoid(1.0, 0.0, 2.0, 8.0, 10.0) == pytest.approx(0.5)
        assert trapezoid(9.0, 0.0, 2.0, 8.0, 10.0) == pytest.approx(0.5)
        assert trapezoid(-1.0, 0.0, 2.0, 8.0, 10.0) == 0.0
        assert trapezoid(11.0, 0.0, 2.0, 8.0, 10.0) == 0.0

    def test_monolayer(self, monolayer_raman):
        x, y = monolayer_raman
        cx, cy = make_c1s([(285.2, 300.0, 1.0), (286.5, 200.0, 1.1), (290.8, 600.0, 1.9)], sp2_area=9000.0)
        ox, oy = make_o1s([(532.7, 130.0, 1.6)])
        result = classify(analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)}))
        assert result.form == "monolayer"
        assert result.confidence > 0.7

    def test_graphene_oxide(self, go_raman, go_xps):
        x, y = go_raman
        (cx, cy), (ox, oy) = go_xps
        result = classify(analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)}))
        assert result.form == "graphene_oxide"
        assert result.confidence > 0.6

    def test_rgo(self, raman_axis):
        y = make_raman(
            raman_axis,
            [(1345.0, 1100.0, 130.0), (1590.0, 1000.0, 88.0), (2690.0, 160.0, 200.0)],
            seed=21,
            noise=4.0,
        )
        cx, cy = make_c1s(
            [(285.2, 900.0, 1.2), (286.6, 1000.0, 1.2), (287.8, 420.0, 1.2), (288.9, 260.0, 1.2)],
            sp2_area=6000.0,
        )
        ox, oy = make_o1s([(531.2, 500.0, 1.7), (532.7, 1100.0, 1.7), (533.9, 250.0, 1.7)])
        result = classify(analyse_raman(raman_axis, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)}))
        assert result.form == "reduced_graphene_oxide"

    def test_gnp(self, gnp_raman):
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
        ox, oy = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
        result = classify(analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)}))
        assert result.form in {"multilayer_gnp", "few_layer"}

    def test_no_data_is_indeterminate(self):
        result = classify(None, None)
        assert result.form == "indeterminate"
        assert result.confidence == 0.0

    def test_raman_only_warns_about_missing_chemistry(self, gnp_raman):
        x, y = gnp_raman
        result = classify(analyse_raman(x, y, 532.0), None)
        assert any("No XPS data" in w for w in result.warnings)

    def test_truncated_raman_does_not_claim_absent_2d(self):
        """A spectrum that stops at 1800 cm-1 says nothing about the 2D band."""
        x = np.linspace(1100.0, 1800.0, 800)
        y = make_raman(x, [(1350.0, 300.0, 50.0), (1582.0, 1000.0, 24.0)], seed=22)
        from app.analysis.classify import build_facts

        facts = build_facts(analyse_raman(x, y, 532.0), None)
        assert "two_d_absent" not in facts


# --------------------------------------------------------------------------- #
# Modules A and B
# --------------------------------------------------------------------------- #
class TestApplicationRecommender:
    def test_gnp_recommends_composites(self, library, gnp_raman):
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
        ox, oy = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        matches = recommend_applications(
            library["applications"],
            classify(raman, xps),
            raman,
            xps,
            SampleProperties(flake_size_um=9.0, bet_m2_g=140.0),
        )
        assert len(matches) == 3
        assert matches[0].key in {"polymer_composites", "coatings_anticorrosion", "fire_retardants"}
        assert matches[0].verdict == "pass"

    def test_go_fails_electronics_with_reason(self, library, go_raman, go_xps):
        x, y = go_raman
        (cx, cy), (ox, oy) = go_xps
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        matches = recommend_applications(
            library["applications"], classify(raman, xps), raman, xps, None, top_n=0
        )
        electronics = next(m for m in matches if m.key == "electronics_photonics")
        assert electronics.verdict == "fail"
        assert any("C/O" in m for m in electronics.mismatches)
        concrete = next(m for m in matches if m.key == "concrete_cement")
        assert concrete.score > electronics.score

    def test_passing_scores_are_graded_not_tied(self, library, gnp_raman):
        """A sample passing several applications must still rank them."""
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
        ox, oy = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        matches = recommend_applications(
            library["applications"],
            classify(raman, xps),
            raman,
            xps,
            SampleProperties(flake_size_um=9.0, bet_m2_g=140.0),
            top_n=0,
        )
        passing = [m for m in matches if m.verdict == "pass"]
        assert len(passing) >= 2
        assert len({m.score for m in passing}) == len(passing), "scores tied"
        # A pass must always outrank any borderline or failing application.
        worst_pass = min(m.score for m in passing)
        others = [m.score for m in matches if m.verdict in {"borderline", "fail"}]
        assert all(worst_pass > score for score in others)

    def test_verdict_reflects_criteria_not_a_score_threshold(self, library):
        from app.models.analysis import Classification

        classification = Classification(
            form="multilayer_gnp", label="Multilayer / GNP", confidence=0.8
        )
        raman = analyse_raman(*_gnp_arrays(), 532.0)
        matches = recommend_applications(
            library["applications"],
            classification,
            raman,
            None,
            SampleProperties(bet_m2_g=140.0, flake_size_um=9.0),
            top_n=0,
        )
        for match in matches:
            evaluated = [c for c in match.checks if c.verdict != "unknown"]
            if evaluated and all(c.verdict == "pass" for c in evaluated) and not match.mismatches:
                assert match.verdict == "pass", match.name

    def test_unmeasured_criteria_are_marked_unknown(self, library, gnp_raman):
        x, y = gnp_raman
        raman = analyse_raman(x, y, 532.0)
        matches = recommend_applications(
            library["applications"], classify(raman, None), raman, None, None, top_n=0
        )
        composites = next(m for m in matches if m.key == "polymer_composites")
        unknown = [c for c in composites.checks if c.verdict == "unknown"]
        assert {"co_ratio", "bet_m2_g", "flake_size_um"} <= {c.metric for c in unknown}


class TestPeerMatching:
    def test_gnp_matches_gnp_products(self, library, gnp_raman):
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
        ox, oy = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        peers = match_peers(
            library["products"],
            classify(raman, xps),
            raman,
            xps,
            SampleProperties(flake_size_um=9.0, bet_m2_g=140.0),
        )
        assert len(peers) == 3
        assert all(p.form in {"gnp_few_layer", "ink_dispersion", "flash_turbostratic"} for p in peers)
        assert peers[0].similarity_pct >= peers[-1].similarity_pct
        assert peers[0].datasheet_url

    def test_go_matches_go_products(self, library, go_raman, go_xps):
        x, y = go_raman
        (cx, cy), (ox, oy) = go_xps
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        peers = match_peers(library["products"], classify(raman, xps), raman, xps, None)
        assert peers[0].form == "graphene_oxide"

    def test_oxygen_phrasing_never_exceeds_100_percent_lower(self, library, gnp_raman):
        """'136% lower oxygen content' is not a quantity that can exist."""
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 200.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=9000.0)
        ox, oy = make_o1s([(532.7, 60.0, 1.6)])
        raman, xps = analyse_raman(x, y, 532.0), analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)})
        peers = match_peers(
            library["products"], classify(raman, xps), raman, xps, SampleProperties(bet_m2_g=140.0)
        )
        assert peers
        for peer in peers:
            for magnitude, direction in re.findall(
                r"(\d+(?:\.\d+)?)%\s+(higher|lower)", peer.summary
            ):
                if direction == "lower":
                    assert float(magnitude) <= 100.0, peer.summary

    def test_no_metrics_returns_nothing(self, library):
        from app.models.analysis import Classification

        classification = Classification(form="few_layer", label="Few-layer", confidence=0.5)
        assert match_peers(library["products"], classification, None, None, None) == []


# --------------------------------------------------------------------------- #
# Scorecard, rankings, full payload
# --------------------------------------------------------------------------- #
class TestReportAssembly:
    def test_scorecard_verdicts(self):
        rows = build_scorecard("few_layer", {"id_ig": 0.3, "co_ratio": 20.0, "bet_m2_g": 1200.0})
        by_metric = {r.metric: r for r in rows}
        assert by_metric["id_ig"].verdict == "pass"
        assert by_metric["co_ratio"].verdict == "pass"
        assert by_metric["bet_m2_g"].verdict == "fail"

    def test_scorecard_borderline(self):
        rows = build_scorecard("few_layer", {"id_ig": 0.55})
        assert rows[0].verdict == "borderline"

    def test_ranking_direction(self, library):
        # Low I(D)/I(G) is good, so a pristine sample must rank high.
        low = build_rankings({"id_ig": 0.02}, library["spectra"])[0]
        high = build_rankings({"id_ig": 1.5}, library["spectra"])[0]
        assert low.percentile > high.percentile
        assert low.better_is_lower is True

        # High C/O is good, so the direction must flip.
        rich = build_rankings({"co_ratio": 50.0}, library["spectra"])[0]
        poor = build_rankings({"co_ratio": 2.0}, library["spectra"])[0]
        assert rich.percentile > poor.percentile
        assert rich.better_is_lower is False

    def test_full_payload(self, library, gnp_raman):
        x, y = gnp_raman
        cx, cy = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
        ox, oy = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
        payload = build_report_payload(
            analyse_raman(x, y, 532.0),
            analyse_xps({"c1s": (cx, cy), "o1s": (ox, oy)}),
            SampleProperties(flake_size_um=9.0, bet_m2_g=140.0, youngs_modulus_gpa=900.0),
            library["spectra"],
            library["properties"],
            library["applications"],
            library["products"],
        )
        assert payload["classification"].form in {"multilayer_gnp", "few_layer"}
        assert len(payload["applications"]) == 3
        assert len(payload["peers"]) == 3
        assert payload["scorecard"]
        assert payload["rankings"]
        assert payload["dft"] is not None
        assert payload["citations"]
        # Every citation must carry something a reader can follow up.
        assert all(c["source"] for c in payload["citations"])
        assert any(c.get("doi") or c.get("url") for c in payload["citations"])

    def test_dft_picks_closest_reference(self, library):
        payload = build_report_payload(
            None,
            None,
            SampleProperties(carrier_mobility_cm2_vs=11000.0),
            library["spectra"],
            library["properties"],
            library["applications"],
            library["products"],
        )
        comparison = payload["dft"].comparisons[0]
        # 11 000 is near the supported-on-SiO2 value, not the suspended one.
        assert "SiO2" in comparison.reference_material
        assert comparison.verdict == "pass"

    def test_missing_properties_warns(self, library, gnp_raman):
        x, y = gnp_raman
        payload = build_report_payload(
            analyse_raman(x, y, 532.0), None, None,
            library["spectra"], library["properties"], library["applications"], library["products"],
        )
        assert any("BET" in w for w in payload["warnings"])


class TestSeedLibrary:
    def test_every_record_has_provenance(self, library):
        for record in library["spectra"] + library["properties"]:
            provenance = record["provenance"]
            assert provenance["source"]
            assert provenance["version"]
            assert provenance["retrieved"]
            assert provenance.get("doi") or provenance.get("url")

    def test_keys_are_unique(self, library):
        for name, records in library.items():
            keys = [r["key"] for r in records]
            assert len(keys) == len(set(keys)), f"duplicate keys in {name}"

    def test_products_have_comparable_specs(self, library):
        for product in library["products"]:
            assert product["specs"], product["key"]
            assert product["datasheet_url"]
            assert product["form"] in {
                "cvd_monolayer_film", "gnp_few_layer", "graphene_oxide",
                "rgo", "flash_turbostratic", "ink_dispersion",
            }

    def test_application_criteria_are_well_formed(self, library):
        for app in library["applications"]:
            assert app["criteria"]
            for metric, spec in app["criteria"].items():
                assert "min" in spec or "max" in spec, metric
                if "min" in spec and "max" in spec:
                    assert spec["min"] < spec["max"], metric

    def test_curve_synthesis(self, library):
        from seed.seed import build_curve

        for record in library["spectra"]:
            cx, cy = build_curve(record)
            assert len(cx) == len(cy) > 100
            assert max(cy) > 0
