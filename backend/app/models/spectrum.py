from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import MongoModel

Technique = Literal["raman", "xps", "dft"]
XPSRegion = Literal["survey", "c1s", "o1s", "unknown"]


class ParseWarning(BaseModel):
    code: str
    message: str


class SpectrumMeta(BaseModel):
    """Everything the parser inferred about a file, kept for auditability."""

    filename: str
    technique: Technique
    region: XPSRegion = "unknown"
    x_label: str
    y_label: str
    x_unit: str
    n_points: int
    x_min: float
    x_max: float
    delimiter: str | None = None
    header_lines: int = 0
    detected_format: str = "generic_tabular"
    columns_detected: dict[str, str] = Field(default_factory=dict)
    instrument_hints: dict[str, str] = Field(default_factory=dict)
    excitation_nm: float | None = None
    warnings: list[ParseWarning] = Field(default_factory=list)


class Spectrum(MongoModel):
    id: str
    sample_id: str
    user_id: str
    technique: Technique
    region: XPSRegion = "unknown"
    meta: SpectrumMeta
    storage_key: str
    # Downsampled copy for plotting; the authoritative data is the stored file.
    preview_x: list[float] = Field(default_factory=list)
    preview_y: list[float] = Field(default_factory=list)
    created_at: datetime


class SpectrumPublic(MongoModel):
    id: str
    sample_id: str
    technique: Technique
    region: XPSRegion
    meta: SpectrumMeta
    preview_x: list[float]
    preview_y: list[float]
    created_at: datetime
