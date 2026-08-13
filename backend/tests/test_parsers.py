import numpy as np
import pytest

from app.parsers.raman import parse_raman
from app.parsers.spec import parse_spec
from app.parsers.tabular import ParseError, parse_table, sniff_delimiter
from app.parsers.xps import classify_region, parse_xps
from tests.conftest import csv_bytes, lorentzian


@pytest.fixture
def spectrum():
    x = np.linspace(1000.0, 3000.0, 1200)
    y = (
        lorentzian(x, 1350.0, 300.0, 45.0)
        + lorentzian(x, 1582.0, 1000.0, 20.0)
        + lorentzian(x, 2700.0, 600.0, 35.0)
        + 50.0
    )
    return x, y


class TestDelimiterSniffing:
    def test_detects_tab(self):
        lines = ["1000.0\t50.0", "1001.0\t51.0", "1002.0\t52.0"]
        assert sniff_delimiter(lines) == ("\t", "tab")

    def test_detects_comma(self):
        lines = ["1000.0,50.0", "1001.0,51.0", "1002.0,52.0"]
        assert sniff_delimiter(lines) == (",", "comma")

    def test_detects_whitespace(self):
        lines = ["1000.0   50.0", "1001.0   51.0", "1002.0   52.0"]
        assert sniff_delimiter(lines) == (None, "whitespace")

    def test_semicolon_with_decimal_comma(self):
        # A comma-decimal locale makes every token fail a plain number test;
        # the sniffer must still identify ';' as the separator.
        lines = ["1000,0;50,5", "1001,0;51,5", "1002,0;52,5"]
        assert sniff_delimiter(lines) == (";", "semicolon")


class TestRamanParsing:
    def test_plain_two_column_csv(self, spectrum):
        x, y = spectrum
        px, py, meta = parse_raman(csv_bytes(x, y), "test.csv")
        assert meta.n_points == len(x)
        assert meta.x_unit == "cm-1"
        np.testing.assert_allclose(px, x, rtol=1e-4)

    def test_descending_wavenumber_is_sorted(self, spectrum):
        x, y = spectrum
        px, _py, _meta = parse_raman(csv_bytes(x[::-1], y[::-1]), "desc.csv")
        assert np.all(np.diff(px) > 0)

    def test_renishaw_header_yields_named_columns_and_laser(self, spectrum):
        x, y = spectrum
        header = "#Renishaw WiRE ASCII export\n#Laser: 532 nm\n#Acq. time = 10 s\n#Wave\t#Intensity"
        _px, _py, meta = parse_raman(csv_bytes(x, y, header, "\t"), "wire.txt")
        assert meta.detected_format == "renishaw_wire_ascii"
        assert meta.excitation_nm == 532.0
        assert meta.instrument_hints["vendor"] == "Renishaw"
        # The commented header row must be recovered as column names.
        assert "Wave" in meta.columns_detected["x"]

    def test_horiba_header(self, spectrum):
        x, y = spectrum
        _px, _py, meta = parse_raman(
            csv_bytes(x, y, "#LabSpec 6 export\n#Excitation wavelength = 633 nm", "\t"), "h.txt"
        )
        assert meta.detected_format == "horiba_labspec_ascii"
        assert meta.excitation_nm == 633.0

    def test_decimal_comma_semicolon(self, spectrum):
        x, y = spectrum
        body = "\n".join(f"{a:.3f};{b:.3f}".replace(".", ",") for a, b in zip(x, y))
        px, py, meta = parse_raman(body.encode(), "eu.csv")
        assert meta.delimiter == "semicolon"
        assert px[0] == pytest.approx(1000.0, abs=0.01)
        assert py.max() > 900

    def test_map_export_is_averaged(self, spectrum):
        x, y = spectrum
        rows = [
            f"{px}\t0.0\t{a:.3f}\t{b:.3f}"
            for px in (0.0, 1.0, 2.0)
            for a, b in zip(x[::4], y[::4])
        ]
        raw = ("X\tY\tWavenumber\tIntensity\n" + "\n".join(rows)).encode()
        ax, _ay, meta = parse_raman(raw, "map.txt")
        assert meta.detected_format.endswith("_map")
        assert ax.size == len(x[::4])
        assert any(w.code == "map_averaged" for w in meta.warnings)

    def test_supplied_excitation_overrides_header_with_warning(self, spectrum):
        x, y = spectrum
        _px, _py, meta = parse_raman(
            csv_bytes(x, y, "#Laser: 633 nm"), "conflict.csv", excitation_nm=532.0
        )
        assert meta.excitation_nm == 532.0
        assert any(w.code == "excitation_conflict" for w in meta.warnings)

    def test_missing_excitation_warns(self, spectrum):
        x, y = spectrum
        _px, _py, meta = parse_raman(csv_bytes(x, y), "bare.csv")
        assert any(w.code == "excitation_default" for w in meta.warnings)

    def test_narrow_range_warns(self):
        x = np.linspace(2500.0, 2900.0, 400)
        y = lorentzian(x, 2700.0, 100.0, 40.0) + 10.0
        _px, _py, meta = parse_raman(csv_bytes(x, y), "narrow.csv")
        assert any(w.code == "range" for w in meta.warnings)

    def test_binary_file_rejected(self):
        with pytest.raises(ParseError, match="binary"):
            parse_raman(b"\x00\x01\x02" * 500, "x.wdf")

    def test_single_column_rejected(self):
        with pytest.raises(ParseError):
            parse_raman(b"\n".join(b"%f" % v for v in range(50)), "one.csv")

    def test_too_few_points_rejected(self):
        with pytest.raises(ParseError, match="at least 10"):
            parse_raman(b"1000,1\n1001,2\n1002,3", "tiny.csv")

    def test_nonnumeric_rows_dropped(self, spectrum):
        x, y = spectrum
        body = csv_bytes(x, y).decode().split("\n")
        body.insert(10, "bad,data")
        _px, _py, meta = parse_raman("\n".join(body).encode(), "dirty.csv")
        # All real rows survive; only the injected junk row is dropped.
        assert meta.n_points == len(x)
        assert any("non-numeric" in w.message for w in meta.warnings)


class TestXPSParsing:
    @pytest.fixture
    def c1s(self):
        x = np.linspace(280.0, 295.0, 600)
        y = lorentzian(x, 284.5, 10000.0, 1.2) + lorentzian(x, 286.5, 1500.0, 1.5) + 200.0
        return x, y

    def test_region_classification(self):
        assert classify_region(281.0, 293.0) == "c1s"
        assert classify_region(527.0, 540.0) == "o1s"
        assert classify_region(0.0, 1200.0) == "survey"
        assert classify_region(395.0, 405.0) == "unknown"

    def test_named_binding_energy_column(self, c1s):
        x, y = c1s
        results = parse_xps(csv_bytes(x[::-1], y[::-1], "Binding Energy (eV),Counts"), "c.csv")
        assert len(results) == 1
        px, _py, meta = results[0]
        assert meta.region == "c1s"
        assert np.all(np.diff(px) > 0)

    def test_kinetic_energy_converted(self, c1s):
        x, y = c1s
        ke = 1486.6 - x
        results = parse_xps(
            csv_bytes(ke, y, "# Source: Al Kalpha\nKinetic Energy,CPS"), "ke.csv"
        )
        px, _py, meta = results[0]
        assert meta.region == "c1s"
        assert px[0] == pytest.approx(280.0, abs=0.05)
        assert "conversion" in meta.columns_detected

    def test_region_hint_respected(self, c1s):
        x, y = c1s
        results = parse_xps(csv_bytes(x, y), "unknown.csv", region_hint="c1s")
        assert results[0][2].region == "c1s"


class TestVamas:
    def _build(self) -> bytes:
        """Minimal but spec-conformant NORM/REGULAR single-block VAMAS file."""
        n = 60
        values = [str(1000 + i * 3) for i in range(n)]
        lines = [
            "VAMAS Surface Chemical Analysis Standard Data Transfer Format 1988 May 4",
            "Test Institute",
            "Test Instrument",
            "operator",
            "experiment",
            "0",            # comment lines
            "NORM",         # experiment mode
            "REGULAR",      # scan mode
            "1",            # number of spectral regions
            "0",            # number of experimental variables
            "0",            # parameter inclusion/exclusion entries
            "0",            # manually entered items
            "0",            # future upgrade experiment entries
            "0",            # future upgrade block entries
            "1",            # number of blocks
            "block1",
            "sample1",
            "2026", "8", "12", "10", "30", "0", "0",
            "0",            # block comment lines
            "XPS",
            "Al",           # analysis source label
            "1486.6",       # source characteristic energy
            "200.0",        # source strength
            "1000.0", "1000.0",   # beam width x, y
            "0.0", "0.0",   # source polar angle, azimuth
            "FAT",          # analyser mode
            "20.0",         # pass energy
            "1.0",          # magnification
            "4.5",          # work function
            "0.0",          # target bias
            "1000.0", "1000.0",   # analysis width x, y
            "0.0", "0.0",   # take-off polar angle, azimuth
            "C",            # species label
            "1s",           # transition label
            "-1",           # charge of detected particle
            "Binding Energy",
            "eV",
            "281.0",        # abscissa start
            "0.2",          # abscissa increment
            "1",            # number of corresponding variables
            "Intensity", "counts",
            "pulse counting",
            "1.0",          # collection time
            "1",            # number of scans
            "0.0",          # signal time correction
            "0.0", "0.0",   # sample normal tilt polar angle, azimuth
            "0.0",          # sample rotation angle
            "0",            # additional numerical parameters
            str(n),         # number of ordinate values
            "1000.0", "1180.0",   # min/max of the corresponding variable
            *values,
            "end of experiment",
        ]
        return "\n".join(lines).encode()

    def test_parses_single_block(self):
        results = parse_xps(self._build(), "test.vms")
        assert len(results) == 1
        x, y, meta = results[0]
        assert meta.detected_format == "vamas_iso14976"
        assert meta.region == "c1s"
        assert x.size == 60
        # abscissa_start 281.0 with increment 0.2 over 60 points
        assert x[0] == pytest.approx(281.0)
        assert x[-1] == pytest.approx(281.0 + 0.2 * 59)
        assert y[0] == pytest.approx(1000.0)

    def test_detected_by_extension(self):
        from app.parsers.vamas import is_vamas

        assert is_vamas(b"anything", "file.vms")
        assert is_vamas(self._build(), "file.txt")
        assert not is_vamas(b"1,2\n3,4", "file.csv")

    def test_truncated_file_raises(self):
        raw = b"\n".join(self._build().split(b"\n")[:20])
        with pytest.raises(ParseError):
            parse_xps(raw, "trunc.vms")


class TestSpecParsing:
    def test_json_with_units(self):
        raw = (
            b'{"BET": "250 m2/g", "flake size": {"value": 5000, "unit": "nm"}, '
            b'"Youngs modulus": "1.0 TPa", "unrelated": "abc"}'
        )
        props, pairs, warnings = parse_spec(raw, "s.json")
        assert props.bet_m2_g == 250.0
        assert props.flake_size_um == 5.0     # 5000 nm converted
        assert props.youngs_modulus_gpa == 1000.0   # TPa converted
        assert "unrelated" in pairs

    def test_csv_three_column_with_units(self):
        raw = b"Property,Value,Unit\nBET surface area,250,m2/g\nD50,10,um\nCarbon purity,99.5,%\n"
        props, _pairs, _warnings = parse_spec(raw, "s.csv")
        assert props.bet_m2_g == 250.0
        assert props.flake_size_um == 10.0
        assert props.carbon_purity_pct == 99.5

    def test_wide_csv_layout(self):
        raw = b"band gap,youngs modulus,carrier mobility\n0.0,1000,15000\n"
        props, _pairs, _warnings = parse_spec(raw, "wide.csv")
        assert props.band_gap_ev == 0.0
        assert props.youngs_modulus_gpa == 1000.0
        assert props.carrier_mobility_cm2_vs == 15000.0

    def test_unit_in_key_is_used(self):
        props, _pairs, _warnings = parse_spec(b'{"BET (m2/g)": 300}', "k.json")
        assert props.bet_m2_g == 300.0

    def test_nested_json_flattened(self):
        raw = b'{"physical": {"BET": 200, "thickness": "2 nm"}, "electronic": {"band gap": 0}}'
        props, _pairs, _warnings = parse_spec(raw, "n.json")
        assert props.bet_m2_g == 200.0
        assert props.thickness_nm == 2.0

    def test_carbon_purity_not_shadowed_by_purity(self):
        # "purity" is also an alias; the longer match must win.
        props, _pairs, _warnings = parse_spec(b'{"carbon purity": 98.5}', "p.json")
        assert props.carbon_purity_pct == 98.5

    def test_unrecognised_keys_warn(self):
        _props, _pairs, warnings = parse_spec(b'{"colour": "black"}', "c.json")
        assert any("No recognised properties" in w for w in warnings)

    def test_invalid_json_raises(self):
        with pytest.raises(ParseError, match="Invalid JSON"):
            parse_spec(b"{not json", "bad.json")

    def test_out_of_range_value_rejected(self):
        with pytest.raises(ParseError, match="validation"):
            parse_spec(b'{"carbon purity": 150}', "bad.json")


class TestTableEdgeCases:
    def test_trailing_delimiters_dropped(self):
        raw = b"\n".join(b"%d,%d," % (i, i * 2) for i in range(1000, 1050))
        table = parse_table(raw)
        assert table.frame.shape[1] == 2

    def test_blank_lines_tolerated(self):
        rows = "\n\n".join(f"{i},{i*2}" for i in range(1000, 1050))
        table = parse_table(rows.encode())
        assert len(table.frame) == 50

    def test_latin1_fallback(self):
        raw = "1000,1\n1001,2\n".encode("latin-1") + b"\xe9\n"
        # The stray byte becomes a non-numeric row and is dropped, not fatal.
        with pytest.raises(ParseError):
            parse_table(raw)
