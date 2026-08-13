from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import MongoModel

ProductionRoute = Literal[
    "cvd",
    "liquid_phase_exfoliation",
    "mechanical_exfoliation",
    "electrochemical_exfoliation",
    "hummers_oxidation",
    "thermal_reduction",
    "chemical_reduction",
    "flash_joule_heating",
    "plasma",
    "sic_sublimation",
    "other",
    "unknown",
]


class SampleProperties(BaseModel):
    """Optional user-declared / measured properties used by the peer matcher."""

    flake_size_um: float | None = Field(default=None, gt=0, le=10_000)
    bet_m2_g: float | None = Field(default=None, gt=0, le=3000)
    thickness_nm: float | None = Field(default=None, gt=0)
    bulk_density_g_cm3: float | None = Field(default=None, gt=0)
    carbon_purity_pct: float | None = Field(default=None, ge=0, le=100)
    band_gap_ev: float | None = Field(default=None, ge=0, le=20)
    youngs_modulus_gpa: float | None = Field(default=None, gt=0)
    carrier_mobility_cm2_vs: float | None = Field(default=None, gt=0)
    sheet_resistance_ohm_sq: float | None = Field(default=None, gt=0)


class SampleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    production_route: ProductionRoute = "unknown"
    feedstock: str | None = Field(default=None, max_length=200)
    batch_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)
    properties: SampleProperties = Field(default_factory=SampleProperties)


class SampleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    production_route: ProductionRoute | None = None
    feedstock: str | None = Field(default=None, max_length=200)
    batch_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)
    properties: SampleProperties | None = None


class Sample(MongoModel):
    id: str
    user_id: str
    name: str
    production_route: ProductionRoute = "unknown"
    feedstock: str | None = None
    batch_id: str | None = None
    notes: str | None = None
    properties: SampleProperties = Field(default_factory=SampleProperties)
    created_at: datetime
    updated_at: datetime
