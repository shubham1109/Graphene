"""ISO 14976 (VAMAS) surface chemical analysis file reader.

Implements the line-ordered block structure for the experiment modes that XPS
instruments actually emit (NORM, MAP, MAPDP, SDP) with REGULAR scans. Blocks
using IRREGULAR scan mode are read but flagged, since the abscissa then lives
in a corresponding-variable array rather than start/increment fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.parsers.tabular import ParseError, decode_bytes

VAMAS_MAGIC = "VAMAS Surface Chemical Analysis Standard Data Transfer Format 1988 May 4"

_MAP_MODES = {"MAP", "MAPDP"}
_SPUTTER_MODES = {"MAPDP", "MAPSVDP", "SDP", "SDPSF"}
_SPUTTER_TECHNIQUES = {"FABMS", "FABMS energy spec", "ISS", "SIMS", "SIMS energy spec", "SNMS", "SNMS energy spec"}
_FOV_MODES = {"MAP", "MAPDP", "MAPSV", "MAPSVDP", "SEM"}
_LINESCAN_MODES = {"MAPSV", "MAPSVDP", "SEM"}
_SPUTTER_XPS_TECHNIQUES = {"AES diff", "AES dir", "EDX", "ELS", "UPS", "XPS", "XRF"}


@dataclass
class VamasBlock:
    block_id: str
    sample_id: str
    technique: str
    species: str
    transition: str
    abscissa_label: str
    abscissa_units: str
    x: np.ndarray
    y: np.ndarray
    corresponding_variables: list[str] = field(default_factory=list)
    analysis_source_energy: float | None = None
    scan_mode: str = "REGULAR"
    n_scans: int = 1
    dwell_s: float | None = None
    warnings: list[str] = field(default_factory=list)


class _Cursor:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._i = 0

    def next(self) -> str:
        if self._i >= len(self._lines):
            raise ParseError(
                f"VAMAS file ended unexpectedly at line {self._i + 1}; the block "
                "structure is truncated or uses an unsupported experiment mode."
            )
        value = self._lines[self._i].strip()
        self._i += 1
        return value

    def skip(self, count: int) -> None:
        for _ in range(count):
            self.next()

    def next_int(self) -> int:
        raw = self.next()
        try:
            return int(float(raw))
        except ValueError:
            raise ParseError(f"Expected an integer in VAMAS file, got {raw!r}.") from None

    def next_float(self) -> float:
        raw = self.next().replace("D", "E").replace("d", "e")
        try:
            return float(raw)
        except ValueError:
            raise ParseError(f"Expected a number in VAMAS file, got {raw!r}.") from None

    @property
    def remaining(self) -> int:
        return len(self._lines) - self._i


def is_vamas(raw: bytes, filename: str) -> bool:
    if filename.lower().endswith((".vms", ".vamas", ".npl")):
        return True
    head = raw[:200].decode("latin-1", errors="ignore")
    return VAMAS_MAGIC[:40] in head


def parse_vamas(raw: bytes) -> list[VamasBlock]:
    text, _ = decode_bytes(raw)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cur = _Cursor(lines)

    identifier = cur.next()
    if VAMAS_MAGIC[:40] not in identifier:
        raise ParseError("File does not start with the VAMAS 1988 format identifier.")

    cur.skip(4)  # institution, instrument model, operator, experiment identifier
    cur.skip(cur.next_int())  # experiment comment lines

    experiment_mode = cur.next()
    scan_mode = cur.next()

    if experiment_mode in {"MAP", "MAPDP", "NORM", "SDP"}:
        cur.next_int()  # number of spectral regions
    if experiment_mode in _MAP_MODES:
        cur.skip(3)  # analysis positions, discrete x, discrete y

    n_exp_vars = cur.next_int()
    cur.skip(2 * n_exp_vars)  # label + unit per experimental variable

    # Parameter inclusion/exclusion list: the sign selects inclusion vs
    # exclusion, the magnitude is the number of prefix numbers that follow.
    cur.skip(abs(cur.next_int()))
    # Manually entered items: one prefix number each.
    cur.skip(cur.next_int())
    # Future upgrade experiment entries: one line each.
    cur.skip(cur.next_int())
    # Future upgrade block entries: a count only; the entries live in each block.
    n_future_block = cur.next_int()

    n_blocks = cur.next_int()

    blocks: list[VamasBlock] = []
    for index in range(n_blocks):
        try:
            blocks.append(
                _parse_block(cur, experiment_mode, scan_mode, n_exp_vars, n_future_block)
            )
        except ParseError as exc:
            if blocks:
                blocks[-1].warnings.append(
                    f"Stopped after block {index}: {exc}"
                )
                break
            raise
    if not blocks:
        raise ParseError("VAMAS file contained no readable data blocks.")
    return blocks


def _parse_block(
    cur: _Cursor,
    experiment_mode: str,
    scan_mode: str,
    n_exp_vars: int,
    n_future_block: int = 0,
) -> VamasBlock:
    block_id = cur.next()
    sample_id = cur.next()
    cur.skip(7)  # year, month, day, hour, minute, second, GMT offset
    cur.skip(cur.next_int())  # block comment lines

    technique = cur.next()
    if experiment_mode in _MAP_MODES:
        cur.skip(2)  # x, y coordinate
    cur.skip(n_exp_vars)  # experimental variable values

    cur.next()  # analysis source label
    if experiment_mode in _SPUTTER_MODES or technique in _SPUTTER_TECHNIQUES:
        cur.skip(3)  # sputtering ion Z, atom count, charge

    source_energy = cur.next_float()
    cur.skip(3)  # source strength, beam width x, beam width y

    if experiment_mode in _FOV_MODES:
        cur.skip(2)
    if experiment_mode in _LINESCAN_MODES:
        cur.skip(5)

    cur.skip(2)  # source polar angle, azimuth
    cur.next()  # analyser mode
    cur.next_float()  # pass energy / retard ratio / mass resolution
    if technique == "AES diff":
        cur.next_float()  # differential width
    cur.skip(2)  # magnification, work function
    cur.next_float()  # target bias
    cur.skip(2)  # analysis width x, y
    cur.skip(2)  # take-off polar angle, azimuth

    species = cur.next()
    transition = cur.next()
    cur.next_float()  # charge of detected particle

    warnings: list[str] = []
    abscissa_label, abscissa_units = "", ""
    abscissa_start = abscissa_increment = 0.0
    if scan_mode == "REGULAR":
        abscissa_label = cur.next()
        abscissa_units = cur.next()
        abscissa_start = cur.next_float()
        abscissa_increment = cur.next_float()
    else:
        warnings.append(
            f"Scan mode {scan_mode!r} is not REGULAR; the abscissa was reconstructed "
            "as a point index and may not be in eV."
        )

    n_corresponding = cur.next_int()
    corresponding: list[str] = []
    for _ in range(n_corresponding):
        label = cur.next()
        cur.next()  # units
        corresponding.append(label)

    cur.next()  # signal mode
    dwell = cur.next_float()  # signal collection time
    n_scans = cur.next_int()
    cur.next_float()  # signal time correction

    if technique in _SPUTTER_XPS_TECHNIQUES and experiment_mode in _SPUTTER_MODES:
        cur.skip(7)

    cur.skip(2)  # sample normal tilt polar angle, azimuth
    cur.next_float()  # sample rotation angle

    n_extra = cur.next_int()
    cur.skip(n_extra * 3)  # label, units, value per additional parameter
    cur.skip(n_future_block)

    n_ordinates = cur.next_int()
    cur.skip(2 * n_corresponding)  # min/max per corresponding variable

    values = np.empty(n_ordinates, dtype=float)
    for i in range(n_ordinates):
        values[i] = cur.next_float()

    stride = max(n_corresponding, 1)
    y = values[::stride] if stride > 1 else values
    n_points = len(y)

    if scan_mode == "REGULAR":
        x = abscissa_start + abscissa_increment * np.arange(n_points, dtype=float)
    else:
        x = np.arange(n_points, dtype=float)

    return VamasBlock(
        block_id=block_id,
        sample_id=sample_id,
        technique=technique,
        species=species,
        transition=transition,
        abscissa_label=abscissa_label,
        abscissa_units=abscissa_units,
        x=x,
        y=y,
        corresponding_variables=corresponding,
        analysis_source_energy=source_energy,
        scan_mode=scan_mode,
        n_scans=n_scans,
        dwell_s=dwell,
        warnings=warnings,
    )
