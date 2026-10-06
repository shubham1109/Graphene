"""Models for the TDS application finder.

A user types the numbers off their own technical data sheet, exactly as the
sheet quotes them ("20-50", "<10", ">99.5"), and gets back the applications
the market sells material like theirs into, with the evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.analysis import Verdict
from app.models.common import MongoModel

MaterialFormKind = Literal["powder", "dispersion", "paste", "pellet", "unknown"]
Confidence = Literal["high", "medium", "low"]

# Anything a datasheet would quote: a number, or the text as printed.
SpecEntry = float | int | str | None


class SpecInterval(BaseModel):
    low: float | None = None
    high: float | None = None
    text: str = ""


class TdsSpecInput(BaseModel):
    """The user's own TDS. Every numeric field accepts a number or range text."""

    name: str | None = Field(default=None, max_length=200)
    form: MaterialFormKind = "unknown"
    synthesis: str | None = Field(default=None, max_length=200)
    surface_chemistry: str | None = Field(default=None, max_length=200)
    solvent: str | None = Field(default=None, max_length=200)

    lateral_size_um: SpecEntry = None
    layers: SpecEntry = None
    thickness_nm: SpecEntry = None
    bet_m2_g: SpecEntry = None
    carbon_purity_pct: SpecEntry = None
    oxygen_pct: SpecEntry = None
    impurities_pct: SpecEntry = None
    bulk_density_g_cm3: SpecEntry = None
    electrical_conductivity_s_m: SpecEntry = None
    thermal_conductivity_w_mk: SpecEntry = None
    id_ig: SpecEntry = None
    loading_wt_pct: SpecEntry = None
    density_g_ml: SpecEntry = None
    viscosity_cps: SpecEntry = None
    sheet_resistance_ohm_sq: SpecEntry = None
    # Quoted on many sheets but not scored: no vendor in the database quotes
    # them in a comparable way. Kept so the record mirrors the sheet.
    hydrogen_pct: SpecEntry = None
    thermal_stability_c: SpecEntry = None
    orientation: str | None = Field(default=None, max_length=200)
    crystallinity: str | None = Field(default=None, max_length=200)
    solubility: str | None = Field(default=None, max_length=200)

    notes: str | None = Field(default=None, max_length=4000)


class ParameterCheck(BaseModel):
    parameter: str
    label: str
    unit: str
    entered: str
    market_range: str
    typical: float | None = None
    n_products: int
    weight: float
    score: float
    verdict: Verdict
    comment: str


class ProductDelta(BaseModel):
    parameter: str
    label: str
    unit: str
    yours: str
    theirs: str
    relation: Literal["within", "higher", "lower"]


class ProductSimilarity(BaseModel):
    key: str
    company: str
    product: str
    form: str
    similarity_pct: float
    shared_parameters: list[str]
    deltas: list[ProductDelta] = Field(default_factory=list)
    application_tags: list[str] = Field(default_factory=list)
    applications_text: str | None = None
    datasheet_url: str | None = None
    datasheet_file: str | None = None
    summary: str


class ApplicationFit(BaseModel):
    key: str
    name: str
    description: str
    score: float
    verdict: Verdict
    confidence: Confidence
    evidence_count: int
    coverage: float
    form_match: bool
    checks: list[ParameterCheck]
    closest_products: list[ProductSimilarity] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    rationale: str


class MarketPercentile(BaseModel):
    parameter: str
    label: str
    unit: str
    value: float
    percentile: float
    n_products: int
    comment: str


class TdsMatchReport(BaseModel):
    name: str | None = None
    form: MaterialFormKind
    entered: dict[str, SpecInterval]
    applications: list[ApplicationFit]
    closest_products: list[ProductSimilarity]
    market_context: list[MarketPercentile]
    warnings: list[str] = Field(default_factory=list)
    database_size: int
    market_size: int


class TdsMatchRecord(MongoModel):
    id: str
    user_id: str
    created_at: datetime
    input: TdsSpecInput
    report: TdsMatchReport


class ApplicationProfile(BaseModel):
    """What the market currently sells into one application: the taxonomy
    entry plus the spec envelope derived from every product tagged with it."""

    key: str
    name: str
    description: str
    evidence_count: int
    companies: list[str]
    products: list[str]
    parameter_weights: dict[str, float]
    envelopes: dict[str, dict]
