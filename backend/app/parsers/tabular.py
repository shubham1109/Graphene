"""Delimiter/encoding sniffing and numeric table extraction.

Instrument ASCII exports are wildly inconsistent: Renishaw WiRE writes
tab-separated pairs with descending wavenumbers, HORIBA LabSpec prefixes
metadata with '#', some tools emit European decimal commas, others pad the
file with blank lines or trailing tabs. This module reduces all of that to
(header_text, numeric DataFrame, meta).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

COMMENT_PREFIXES = ("#", "%", ";", "!", "'")
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DELIMITERS = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")
# Same, but with ',' as the decimal mark (German/French instrument locales).
_NUMBER_COMMA = re.compile(r"^[+-]?(\d+,?\d*|,\d+)([eEdD][+-]?\d+)?$")


class ParseError(ValueError):
    """Raised when a file cannot be interpreted as spectral data."""


@dataclass
class TableParse:
    frame: pd.DataFrame
    header_text: str
    delimiter: str | None
    delimiter_name: str
    header_lines: int
    column_names: list[str] | None
    decimal_comma: bool = False
    warnings: list[str] = field(default_factory=list)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Decode with the first encoding that succeeds. Returns (text, encoding)."""
    if b"\x00" in raw[:4096]:
        raise ParseError(
            "File appears to be binary. Native instrument formats (Renishaw .wdf, "
            "Bruker .opus) are not supported yet - please export to CSV or TXT."
        )
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not decode file as text using UTF-8, CP1252 or Latin-1.")


def _is_number(token: str, allow_comma: bool = False) -> bool:
    token = token.strip()
    if _NUMBER.match(token):
        return True
    return bool(allow_comma and _NUMBER_COMMA.match(token))


def _split(line: str, delimiter: str | None) -> list[str]:
    parts = line.split(delimiter) if delimiter else line.split()
    return [p.strip().strip('"').strip() for p in parts]


def _numeric_fraction(tokens: list[str], allow_comma: bool = False) -> float:
    tokens = [t for t in tokens if t != ""]
    if not tokens:
        return 0.0
    return sum(_is_number(t, allow_comma) for t in tokens) / len(tokens)


def sniff_delimiter(lines: list[str]) -> tuple[str | None, str]:
    """Pick the delimiter that yields the most consistent numeric row width."""
    candidates: list[tuple[str | None, str]] = [
        *[(d, name) for d, name in DELIMITERS.items()],
        (None, "whitespace"),
    ]
    best: tuple[float, int, str | None, str] = (-1.0, 0, None, "whitespace")
    for delim, name in candidates:
        widths: list[int] = []
        plain_scores: list[float] = []
        comma_scores: list[float] = []
        for line in lines:
            if delim is not None and delim not in line:
                continue
            tokens = [t for t in _split(line, delim) if t != ""]
            if len(tokens) < 2:
                continue
            widths.append(len(tokens))
            plain_scores.append(_numeric_fraction(tokens))
            comma_scores.append(_numeric_fraction(tokens, allow_comma=True))
        if not widths:
            continue
        modal_width = max(set(widths), key=widths.count)
        consistency = widths.count(modal_width) / len(widths)
        avg_numeric = sum(plain_scores) / len(plain_scores)
        if delim != ",":
            # A ';'-separated file in a comma-decimal locale scores zero under
            # the plain number regex; judge it on its own terms.
            avg_numeric = max(avg_numeric, sum(comma_scores) / len(comma_scores))
        score = consistency * avg_numeric
        # Prefer the higher score; break ties toward more columns resolved.
        if (score, modal_width) > (best[0], best[1]):
            best = (score, modal_width, delim, name)
    if best[0] <= 0:
        return None, "whitespace"
    return best[2], best[3]


def _looks_like_decimal_comma(lines: list[str], delimiter: str | None) -> bool:
    """True when ',' is a decimal mark rather than a separator (e.g. '1580,5')."""
    if delimiter == ",":
        return False
    hits = misses = 0
    for line in lines[:200]:
        for token in _split(line, delimiter):
            if not token:
                continue
            if re.fullmatch(r"[+-]?\d+,\d+", token):
                hits += 1
            elif _is_number(token):
                misses += 1
    return hits > 0 and hits >= misses


def parse_table(raw: bytes) -> TableParse:
    text, _encoding = decode_bytes(raw)
    all_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    header_parts: list[str] = []
    body: list[str] = []
    for line in all_lines:
        stripped = line.strip()
        if not stripped:
            if body:
                body.append(line)
            continue
        if stripped.startswith(COMMENT_PREFIXES):
            (header_parts if not body else body).append(stripped)
            continue
        body.append(line)

    data_lines = [ln for ln in body if ln.strip() and not ln.strip().startswith(COMMENT_PREFIXES)]
    if not data_lines:
        raise ParseError("No data rows found in file.")

    delimiter, delimiter_name = sniff_delimiter(data_lines)
    decimal_comma = _looks_like_decimal_comma(data_lines, delimiter)

    # Leading non-numeric rows are headers. Stop at the first row that is
    # mostly numeric, so a "Wavenumber,Intensity" line is captured as names.
    header_lines = 0
    column_names: list[str] | None = None
    warnings: list[str] = []
    for line in data_lines:
        tokens = [t for t in _split(line, delimiter) if t != ""]
        if len(tokens) >= 2 and _numeric_fraction(tokens, decimal_comma) >= 0.75:
            break
        header_lines += 1
        if len(tokens) >= 2:
            column_names = tokens
        else:
            header_parts.append(line.strip())
        if header_lines > 100:
            raise ParseError("Could not locate numeric data within the first 100 rows.")

    numeric_lines = data_lines[header_lines:]
    if not numeric_lines:
        raise ParseError("File contains headers but no numeric rows.")

    frame = _read_numeric(numeric_lines, delimiter, decimal_comma, warnings)

    if column_names is None:
        column_names = _column_names_from_comment(header_parts, delimiter, frame.shape[1])

    if column_names and len(column_names) == frame.shape[1]:
        frame.columns = [c.strip() for c in column_names]
    else:
        if column_names:
            warnings.append(
                f"Header row had {len(column_names)} names but data has "
                f"{frame.shape[1]} columns; ignoring header names."
            )
            header_parts.append(" ".join(column_names))
        frame.columns = [f"col_{i}" for i in range(frame.shape[1])]
        column_names = None

    return TableParse(
        frame=frame,
        header_text="\n".join(header_parts),
        delimiter=delimiter,
        delimiter_name=delimiter_name,
        header_lines=header_lines,
        column_names=column_names,
        decimal_comma=decimal_comma,
        warnings=warnings,
    )


def _column_names_from_comment(
    header_parts: list[str], delimiter: str | None, n_columns: int
) -> list[str] | None:
    """Recover column names from a commented header row.

    Renishaw WiRE writes '#Wave<TAB>#Intensity' immediately above the data, so
    the names are only reachable after comment stripping. Only the last comment
    line is considered, and only if it splits into exactly n_columns non-numeric
    tokens.
    """
    if not header_parts or n_columns < 2:
        return None
    candidate = header_parts[-1].lstrip("#%;!' ").strip()
    tokens = [t.lstrip("#%;").strip() for t in _split(candidate, delimiter)]
    tokens = [t for t in tokens if t != ""]
    if len(tokens) != n_columns:
        return None
    if _numeric_fraction(tokens, allow_comma=True) > 0.0:
        return None
    header_parts.pop()
    return tokens


def _read_numeric(
    lines: list[str],
    delimiter: str | None,
    decimal_comma: bool,
    warnings: list[str],
) -> pd.DataFrame:
    payload = "\n".join(lines)
    read_kwargs: dict = {
        "header": None,
        "comment": "#",
        "skip_blank_lines": True,
        "engine": "python",
    }
    if delimiter is None:
        read_kwargs["sep"] = r"\s+"
    else:
        read_kwargs["sep"] = re.escape(delimiter)
    if decimal_comma:
        read_kwargs["decimal"] = ","

    try:
        frame = pd.read_csv(io.StringIO(payload), **read_kwargs)
    except Exception as exc:  # pragma: no cover - pandas raises many types
        raise ParseError(f"Could not parse numeric table: {exc}") from exc

    # Drop all-empty columns produced by trailing delimiters.
    frame = frame.dropna(axis=1, how="all")
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(axis=1, how="all")
    before = len(frame)
    frame = frame.dropna(axis=0, how="any")
    dropped = before - len(frame)
    if dropped:
        warnings.append(f"Dropped {dropped} row(s) containing non-numeric values.")
    if frame.shape[1] < 2:
        raise ParseError(
            "Expected at least two numeric columns (x and y); found "
            f"{frame.shape[1]}."
        )
    if len(frame) < 10:
        raise ParseError(f"Only {len(frame)} usable data points; need at least 10.")
    return frame.reset_index(drop=True)
