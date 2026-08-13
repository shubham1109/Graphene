from pydantic import BaseModel, Field

from app.models.common import MongoModel


class Provenance(BaseModel):
    source: str
    doi: str | None = None
    url: str | None = None
    version: str
    licence: str | None = None
    retrieved: str
    note: str | None = None


class ReferenceSpectrum(MongoModel):
    """A curated reference point in Raman/XPS metric space.

    `curve_x`/`curve_y` hold a synthesised representative trace built from the
    published peak parameters so the dashboard can overlay something meaningful
    even before raw dataset files are ingested. `synthetic_curve` records that.
    """

    id: str | None = None
    key: str
    technique: str
    material_class: str
    label: str
    excitation_nm: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    peaks: list[dict] = Field(default_factory=list)
    curve_x: list[float] = Field(default_factory=list)
    curve_y: list[float] = Field(default_factory=list)
    synthetic_curve: bool = True
    provenance: Provenance


class ReferenceProperty(MongoModel):
    id: str | None = None
    key: str
    material: str
    label: str
    properties: dict[str, float] = Field(default_factory=dict)
    provenance: Provenance


class CommercialProduct(MongoModel):
    id: str | None = None
    key: str
    producer: str
    product_name: str
    form: str
    region: str | None = None
    datasheet_url: str | None = None
    specs: dict[str, float] = Field(default_factory=dict)
    spec_notes: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance


class ApplicationSpec(MongoModel):
    id: str | None = None
    key: str
    name: str
    description: str
    target_forms: list[str] = Field(default_factory=list)
    criteria: dict[str, dict] = Field(default_factory=dict)
    notes: str | None = None
