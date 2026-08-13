from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import MongoModel

Verdict = Literal["pass", "borderline", "fail", "unknown"]

MaterialForm = Literal[
    "monolayer",
    "bilayer",
    "few_layer",
    "multilayer_gnp",
    "graphene_oxide",
    "reduced_graphene_oxide",
    "turbostratic",
    "amorphous_carbon",
    "indeterminate",
]

FORM_LABELS: dict[str, str] = {
    "monolayer": "Monolayer (1L)",
    "bilayer": "Bilayer (2L)",
    "few_layer": "Few-layer (3-5L)",
    "multilayer_gnp": "Multilayer / GNP",
    "graphene_oxide": "Graphene oxide (GO)",
    "reduced_graphene_oxide": "Reduced graphene oxide (rGO)",
    "turbostratic": "Turbostratic / flash graphene",
    "amorphous_carbon": "Amorphous / highly disordered carbon",
    "indeterminate": "Indeterminate",
}


# --------------------------------------------------------------------------- #
# Raman
# --------------------------------------------------------------------------- #
class FittedPeak(BaseModel):
    name: str
    center_cm1: float
    height: float
    area: float
    fwhm_cm1: float
    shape: str
    center_stderr: float | None = None
    height_stderr: float | None = None


class RamanResult(BaseModel):
    spectrum_id: str | None = None
    excitation_nm: float
    peaks: list[FittedPeak]
    id_ig: float | None = None
    id_ig_area: float | None = None
    i2d_ig: float | None = None
    i2d_ig_area: float | None = None
    idprime_ig: float | None = None
    fwhm_2d_cm1: float | None = None
    fwhm_g_cm1: float | None = None
    g_position_cm1: float | None = None
    two_d_position_cm1: float | None = None
    # Cancado 2011/2006 defect metrics
    crystallite_size_la_nm: float | None = None
    defect_density_cm2: float | None = None
    mean_defect_distance_nm: float | None = None
    two_d_single_lorentzian: bool | None = None
    two_d_lorentzian_r2: float | None = None
    two_d_four_component_r2: float | None = None
    estimated_layers: str | None = None
    fit_r_squared: float | None = None
    baseline_method: str = "arpls"
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# XPS
# --------------------------------------------------------------------------- #
class XPSComponent(BaseModel):
    name: str
    assignment: str
    binding_energy_ev: float
    area: float
    fwhm_ev: float
    fraction_of_region: float


class XPSResult(BaseModel):
    spectrum_ids: list[str] = Field(default_factory=list)
    regions_analysed: list[str] = Field(default_factory=list)
    c1s_components: list[XPSComponent] = Field(default_factory=list)
    o1s_components: list[XPSComponent] = Field(default_factory=list)
    co_ratio: float | None = None
    co_ratio_source: str | None = None
    oxygen_at_pct: float | None = None
    carbon_at_pct: float | None = None
    sp2_fraction: float | None = None
    sp3_fraction: float | None = None
    sp2_sp3_ratio: float | None = None
    functional_groups: dict[str, float] = Field(default_factory=dict)
    pi_pi_star_present: bool | None = None
    c1s_fit_r_squared: float | None = None
    o1s_fit_r_squared: float | None = None
    background_method: str = "shirley"
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# DFT / property overlay
# --------------------------------------------------------------------------- #
class PropertyComparison(BaseModel):
    property: str
    label: str
    unit: str
    measured: float
    reference_value: float
    reference_material: str
    reference_source: str
    percent_deviation: float
    verdict: Verdict


class DFTResult(BaseModel):
    comparisons: list[PropertyComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
class ClassificationEvidence(BaseModel):
    metric: str
    value: float | None
    supports: str
    weight: float
    detail: str


class Classification(BaseModel):
    form: MaterialForm
    label: str
    confidence: float
    scores: dict[str, float] = Field(default_factory=dict)
    evidence: list[ClassificationEvidence] = Field(default_factory=list)
    layer_estimate: str | None = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Module A - application recommender
# --------------------------------------------------------------------------- #
class CriterionCheck(BaseModel):
    metric: str
    label: str
    measured: float | str | None
    target: str
    verdict: Verdict
    comment: str


class ApplicationMatch(BaseModel):
    key: str
    name: str
    score: float
    verdict: Verdict
    checks: list[CriterionCheck]
    mismatches: list[str] = Field(default_factory=list)
    rationale: str


# --------------------------------------------------------------------------- #
# Module B - commercial peer match
# --------------------------------------------------------------------------- #
class PeerFeatureDelta(BaseModel):
    feature: str
    label: str
    sample_value: float
    product_value: float
    percent_difference: float
    direction: str


class PeerMatch(BaseModel):
    product_id: str
    producer: str
    product_name: str
    form: str
    region: str | None = None
    datasheet_url: str | None = None
    distance: float
    similarity_pct: float
    features_compared: list[str]
    deltas: list[PeerFeatureDelta]
    summary: str


# --------------------------------------------------------------------------- #
# Scorecard + report
# --------------------------------------------------------------------------- #
class ScorecardRow(BaseModel):
    metric: str
    label: str
    value: float | None
    unit: str | None = None
    target_range: str | None = None
    verdict: Verdict
    comment: str
    reference_percentile: float | None = None


class GlobalRanking(BaseModel):
    metric: str
    label: str
    sample_value: float
    percentile: float
    n_references: int
    better_is_lower: bool
    reference_values: list[float] = Field(default_factory=list)
    reference_labels: list[str] = Field(default_factory=list)


class AnalysisReport(MongoModel):
    id: str
    user_id: str
    sample_id: str
    sample_name: str
    created_at: datetime
    engine_version: str
    raman: RamanResult | None = None
    xps: XPSResult | None = None
    dft: DFTResult | None = None
    classification: Classification
    scorecard: list[ScorecardRow] = Field(default_factory=list)
    rankings: list[GlobalRanking] = Field(default_factory=list)
    applications: list[ApplicationMatch] = Field(default_factory=list)
    peers: list[PeerMatch] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
