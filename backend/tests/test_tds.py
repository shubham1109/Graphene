"""TDS application finder: value parsing, the matching engine, and its API."""

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.analysis.specvalue import describe, parse_spec_value
from app.analysis.tds_match import build_profiles, collect_user_specs, match_applications
from app.db import ensure_indexes, get_db
from app.main import app
from app.models.tds import TdsSpecInput
from seed.seed import DATA_DIR, seed

pytestmark = pytest.mark.asyncio


def _load(name: str) -> list[dict]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def database():
    return _load("application_taxonomy.json"), _load("application_products.json"), _load("market_reference.json")


# --------------------------------------------------------------------------- #
# Parsing datasheet numbers
# --------------------------------------------------------------------------- #
class TestSpecValue:
    @pytest.mark.parametrize(
        "text, low, high",
        [
            ("20-50", 20.0, 50.0),
            ("0.02 - 0.2", 0.02, 0.2),
            ("130 to 180", 130.0, 180.0),
            ("<10", None, 10.0),
            ("≤ 1", None, 1.0),
            (">99.5", 99.5, None),
            (">= 97 wt%", 97.0, None),
            ("~300", 300.0, 300.0),
            ("3±0.5", 2.5, 3.5),
            ("O <7", None, 7.0),
            ("O 2.1%, others 1.6%", 2.1, 2.1),
            ("C 99.8%", 99.8, 99.8),
            ("1e5", 1e5, 1e5),
            (5, 5.0, 5.0),
            (2.5, 2.5, 2.5),
        ],
    )
    def test_parses_datasheet_forms(self, text, low, high):
        value = parse_spec_value(text)
        assert value is not None, text
        assert value.low == low and value.high == high

    @pytest.mark.parametrize("text", ["-", "", None, "n/a", "not measured", float("nan")])
    def test_empty_cells_are_none(self, text):
        assert parse_spec_value(text) is None

    def test_scale_applies_to_both_bounds(self):
        value = parse_spec_value("20-50", scale=1e-3)
        assert value.low == pytest.approx(0.02) and value.high == pytest.approx(0.05)

    def test_describe(self):
        assert describe(parse_spec_value("20-50"), "um") == "20 - 50 um"
        assert describe(parse_spec_value("<10"), "um") == "< 10 um"
        assert describe(parse_spec_value(">99.5"), "%") == "> 99.5 %"
        assert describe(parse_spec_value("1500-1980"), "S/m") == "1,500 - 1,980 S/m"
        assert describe(None) == "not specified"


# --------------------------------------------------------------------------- #
# The seeded database itself
# --------------------------------------------------------------------------- #
class TestDatabase:
    def test_every_product_has_key_company_and_specs(self, database):
        _, products, _ = database
        assert len(products) >= 35
        keys = [p["key"] for p in products]
        assert len(keys) == len(set(keys))
        for product in products:
            assert product["company"] and product["product"]
            assert product["form"] in {"powder", "dispersion", "paste", "pellet", "unknown"}
            for value in product["specs"].values():
                assert value["low"] is not None or value["high"] is not None

    def test_all_but_the_general_grade_carry_application_tags(self, database):
        taxonomy, products, _ = database
        known = {t["key"] for t in taxonomy}
        untagged = [p["product"] for p in products if not p["application_tags"]]
        assert untagged == ["Graphene Powder"]
        for product in products:
            assert set(product["application_tags"]) <= known

    def test_date_corrupted_cells_were_recovered(self, database):
        _, products, _ = database
        by_name = {p["product"]: p for p in products}
        assert by_name["GRAP-INK-TI"]["specs"]["layers"] == {"low": 2.0, "high": 3.0, "text": "2-3"}
        assert by_name["xGnP Graphene Nanoplatelets - Grade H"]["specs"]["lateral_size_um"]["text"] == "5-25"
        assert by_name["xGnP Graphene Nanoplatelets - Grade H"]["specs"]["thermal_conductivity_w_mk"]["high"] == 3000.0

    def test_datasheet_overrides_are_recorded(self, database):
        _, products, _ = database
        rga = next(p for p in products if p["product"] == "RGA-COOH-1")
        assert rga["specs"]["lateral_size_um"]["high"] == pytest.approx(0.05)
        assert any(c["field"] == "lateral_size_um" for c in rga["corrections"])
        assert rga["datasheet_file"] and "20-50nm" in rga["datasheet_text"].replace(" ", "")

    def test_pdfs_referenced_exist(self, database):
        _, products, _ = database
        for product in products:
            if product["datasheet_file"]:
                assert (DATA_DIR / "tds_pdfs" / product["datasheet_file"]).is_file()

    def test_market_reference_is_populated(self, database):
        _, _, market = database
        assert len(market) >= 100
        with_bet = [m for m in market if "bet_m2_g" in m["specs"]]
        assert len(with_bet) >= 50

    def test_profiles_have_envelopes(self, database):
        taxonomy, products, _ = database
        profiles = build_profiles(taxonomy, products)
        assert len(profiles) == len(taxonomy)
        composites = next(p for p in profiles if p.key == "polymer_composites")
        assert composites.evidence_count >= 10
        assert "lateral_size_um" in composites.envelopes
        assert composites.envelopes["lateral_size_um"]["n"] >= 10


# --------------------------------------------------------------------------- #
# Matching engine
# --------------------------------------------------------------------------- #
class TestMatching:
    def test_layers_inferred_from_thickness(self):
        values, warnings = collect_user_specs(TdsSpecInput(thickness_nm="1-2"))
        assert values["layers"].low == 3.0 and values["layers"].high == 6.0
        assert any("inferred" in w for w in warnings)

    def test_unreadable_text_is_warned_not_fatal(self):
        values, warnings = collect_user_specs(TdsSpecInput(bet_m2_g="high"))
        assert "bet_m2_g" not in values
        assert warnings

    def test_vendor_grade_matches_its_own_declared_uses(self, database):
        """Hydrograph FGA-1's sheet says composites, resins and corrosion coatings."""
        taxonomy, products, market = database
        report = match_applications(
            TdsSpecInput(
                form="powder", lateral_size_um="0.02-0.05", layers="6", bet_m2_g="130-180",
                carbon_purity_pct="99.8", oxygen_pct="0.2", bulk_density_g_cm3="0.07-0.1",
                electrical_conductivity_s_m="100-300", id_ig="0.68",
            ),
            taxonomy, products, market,
        )
        top = [fit.key for fit in report.applications[:4]]
        assert {"polymer_composites", "coatings_paints", "anticorrosion"} <= set(top)
        assert report.applications[0].verdict == "pass"
        assert report.closest_products[0].product == "FGA-1"
        assert report.closest_products[0].similarity_pct == 100.0
        assert report.market_context and report.database_size == len(products)

    def test_high_surface_area_thin_flakes_point_at_energy_storage(self, database):
        taxonomy, products, market = database
        report = match_applications(
            TdsSpecInput(form="powder", lateral_size_um="<1", layers="3-5", bet_m2_g="450",
                         carbon_purity_pct=">99", oxygen_pct="<1", electrical_conductivity_s_m="3000"),
            taxonomy, products, market,
        )
        ranking = [fit.key for fit in report.applications]
        assert ranking.index("batteries") < ranking.index("lubricants")
        assert ranking.index("batteries") < ranking.index("concrete_cement")
        # Two vendors sell material that reads like this sheet; both must lead.
        assert {p.product for p in report.closest_products[:2]} == {"ADG-A+", "Graphene Nanosheets"}

    def test_oxidised_low_purity_material_goes_to_supercapacitors(self, database):
        taxonomy, products, market = database
        report = match_applications(
            TdsSpecInput(form="powder", lateral_size_um="0.5-10", layers="12", bet_m2_g="470",
                         carbon_purity_pct="77", oxygen_pct="20"),
            taxonomy, products, market,
        )
        assert report.applications[0].key == "supercapacitors"
        assert all(fit.verdict == "fail" for fit in report.applications[1:4])

    def test_absurd_specification_fails_everywhere(self, database):
        taxonomy, products, market = database
        report = match_applications(
            TdsSpecInput(form="powder", lateral_size_um="300", layers="200", bet_m2_g="5", carbon_purity_pct="80"),
            taxonomy, products, market,
        )
        assert all(fit.verdict in {"fail", "unknown"} for fit in report.applications)
        assert report.applications[0].score < 50

    def test_form_mismatch_is_reported_not_fatal(self, database):
        taxonomy, products, market = database
        report = match_applications(
            TdsSpecInput(form="pellet", lateral_size_um="<1", layers="3-5", bet_m2_g="450"),
            taxonomy, products, market,
        )
        supercaps = next(fit for fit in report.applications if fit.key == "supercapacitors")
        assert supercaps.form_match is False
        assert any("Sold as pellet" in gap for gap in supercaps.gaps)

    def test_empty_input_yields_warning_and_no_scores(self, database):
        taxonomy, products, market = database
        report = match_applications(TdsSpecInput(), taxonomy, products, market)
        assert any("No numeric specification" in w for w in report.warnings)
        assert all(fit.score == 0.0 for fit in report.applications)
        assert report.closest_products == []


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient()["graphene_tds_test"]
    await ensure_indexes(database)
    await seed(database)
    return database


@pytest_asyncio.fixture
async def client(db):
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "tds@example.com", "password": "correct horse battery"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


SAMPLE_SPEC = {
    "name": "Batch 7 carbon",
    "form": "powder",
    "lateral_size_um": "0.5-2",
    "layers": "5-8",
    "bet_m2_g": "250",
    "carbon_purity_pct": ">99",
    "oxygen_pct": "<1",
    "electrical_conductivity_s_m": 2000,
}


class TestTdsApi:
    async def test_reference_endpoints_are_public(self, client):
        parameters = await client.get("/api/tds/parameters")
        assert parameters.status_code == 200
        assert {p["key"] for p in parameters.json()} >= {"lateral_size_um", "layers", "bet_m2_g"}

        profiles = await client.get("/api/tds/applications")
        assert profiles.status_code == 200
        assert len(profiles.json()) >= 10
        assert all("envelopes" in p and "evidence_count" in p for p in profiles.json())

        products = await client.get("/api/tds/products?application=batteries")
        assert products.status_code == 200
        assert products.json() and all("batteries" in p["application_tags"] for p in products.json())
        assert all("datasheet_text" not in p for p in products.json())

        with_text = await client.get("/api/tds/products?company=Hydrograph&include_text=true")
        assert any(p.get("datasheet_text") for p in with_text.json())

    async def test_datasheet_download(self, client):
        products = (await client.get("/api/tds/products?company=Hydrograph")).json()
        filename = next(p["datasheet_file"] for p in products if p["datasheet_file"])
        response = await client.get(f"/api/tds/datasheets/{filename}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"
        assert (await client.get("/api/tds/datasheets/../../.env")).status_code in {404, 400}
        assert (await client.get("/api/tds/datasheets/nope.pdf")).status_code == 404

    async def test_template_mirrors_the_fe_8x1_sheet(self, client):
        response = await client.get("/api/tds/template")
        assert response.status_code == 200
        template = response.json()
        assert template["form"] == "powder"
        assert template["layers"] == "7-10" and template["bet_m2_g"] == "190-200"
        assert template["lateral_size_um"] == "0.13-0.17"
        assert template["methods"]["lateral_size_um"].startswith("TEM")

    async def test_template_matches_and_preview_does_not_save(self, client, auth):
        template = (await client.get("/api/tds/template")).json()
        body = {k: v for k, v in template.items() if k not in {"methods", "company", "product_id", "source_file"}}
        preview = await client.post("/api/tds/preview", json=body, headers=auth)
        assert preview.status_code == 200, preview.text
        report = preview.json()
        top = [fit["key"] for fit in report["applications"][:5]]
        assert "polymer_composites" in top or "coatings_paints" in top
        assert report["applications"][0]["verdict"] in {"pass", "borderline"}
        assert report["entered"]["thermal_stability_c"]["text"] == "550 - 600 °C"
        assert (await client.get("/api/tds/matches", headers=auth)).json() == []

    async def test_match_requires_auth(self, client):
        response = await client.post("/api/tds/match", json=SAMPLE_SPEC)
        assert response.status_code == 401

    async def test_match_saves_and_lists(self, client, auth):
        created = await client.post("/api/tds/match", json=SAMPLE_SPEC, headers=auth)
        assert created.status_code == 201, created.text
        record = created.json()
        report = record["report"]
        assert report["applications"] and report["applications"][0]["score"] > 0
        assert report["closest_products"]
        assert report["entered"]["lateral_size_um"]["text"] == "0.5 - 2 um"
        assert record["input"]["name"] == "Batch 7 carbon"

        listed = await client.get("/api/tds/matches", headers=auth)
        assert listed.status_code == 200 and len(listed.json()) == 1

        fetched = await client.get(f"/api/tds/matches/{record['id']}", headers=auth)
        assert fetched.status_code == 200 and fetched.json()["id"] == record["id"]

        deleted = await client.delete(f"/api/tds/matches/{record['id']}", headers=auth)
        assert deleted.status_code == 204
        assert (await client.get(f"/api/tds/matches/{record['id']}", headers=auth)).status_code == 404

    async def test_matches_are_private(self, client, auth):
        await client.post("/api/tds/match", json=SAMPLE_SPEC, headers=auth)
        other = await client.post(
            "/api/auth/register", json={"email": "other@example.com", "password": "another long password"}
        )
        headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
        assert (await client.get("/api/tds/matches", headers=headers)).json() == []

    async def test_validation_rejects_bad_form(self, client, auth):
        response = await client.post("/api/tds/match", json={**SAMPLE_SPEC, "form": "gas"}, headers=auth)
        assert response.status_code == 422
