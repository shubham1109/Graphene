"""End-to-end API tests.

MongoDB is substituted with an in-memory mock so the whole HTTP surface can be
exercised without infrastructure. The production data layer is unchanged: only
the `get_db` dependency is overridden.
"""

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.db import ensure_indexes, get_db
from app.main import app
from app.storage import LocalStorage
from seed.seed import seed
from tests.conftest import csv_bytes, make_c1s, make_o1s, make_raman

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient()["graphene_test"]
    await ensure_indexes(database)
    await seed(database)
    return database


@pytest_asyncio.fixture
async def client(db, tmp_path, monkeypatch):
    import app.storage as storage_module

    monkeypatch.setattr(storage_module, "_storage", LocalStorage(str(tmp_path / "uploads")))
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "Lab@Example.com", "password": "correct horse battery", "full_name": "Test Lab"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Fixture data as uploadable files
# --------------------------------------------------------------------------- #
def gnp_raman_file():
    x = np.linspace(1000.0, 3200.0, 2400)
    y = make_raman(
        x,
        [(1350.0, 280.0, 50.0), (1582.0, 1000.0, 24.0), (1620.0, 100.0, 24.0), (2712.0, 380.0, 76.0)],
        noise=4.0,
        seed=3,
    )
    return csv_bytes(x, y, "#Renishaw WiRE\n#Laser: 532 nm\n#Wave\t#Intensity", "\t")


def c1s_file():
    x, y = make_c1s([(285.3, 700.0, 1.1), (286.6, 400.0, 1.1), (290.8, 350.0, 1.9)], sp2_area=7000.0)
    return csv_bytes(x, y, "Binding Energy (eV),Counts")


def o1s_file():
    x, y = make_o1s([(531.2, 120.0, 1.6), (532.7, 260.0, 1.6)])
    return csv_bytes(x, y, "Binding Energy (eV),Counts")


# --------------------------------------------------------------------------- #
class TestHealthAndReference:
    async def test_reference_endpoints_are_public(self, client):
        for path, minimum in (
            ("/api/reference/spectra", 10),
            ("/api/reference/products", 30),
            ("/api/reference/applications", 7),
            ("/api/reference/properties", 5),
        ):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert len(response.json()) >= minimum, path

    async def test_reference_spectra_include_synthesised_curves(self, client):
        records = (await client.get("/api/reference/spectra?technique=raman")).json()
        assert records
        for record in records:
            assert record["synthetic_curve"] is True
            assert len(record["curve_x"]) == len(record["curve_y"]) > 100
            assert record["provenance"]["source"]

    async def test_curves_can_be_excluded(self, client):
        records = (await client.get("/api/reference/spectra?include_curves=false")).json()
        assert all("curve_x" not in r for r in records)

    async def test_product_filter(self, client):
        records = (await client.get("/api/reference/products?form=graphene_oxide")).json()
        assert records
        assert all(r["form"] == "graphene_oxide" for r in records)


class TestAuth:
    async def test_register_login_me(self, client):
        registration = await client.post(
            "/api/auth/register", json={"email": "a@b.com", "password": "a-long-enough-password"}
        )
        assert registration.status_code == 201
        assert registration.json()["user"]["email"] == "a@b.com"

        login = await client.post(
            "/api/auth/login", json={"email": "a@b.com", "password": "a-long-enough-password"}
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["email"] == "a@b.com"

    async def test_email_is_normalised_to_lowercase(self, client, auth):
        login = await client.post(
            "/api/auth/login", json={"email": "LAB@EXAMPLE.COM", "password": "correct horse battery"}
        )
        assert login.status_code == 200

    async def test_duplicate_email_conflicts(self, client, auth):
        response = await client.post(
            "/api/auth/register", json={"email": "lab@example.com", "password": "another password"}
        )
        assert response.status_code == 409

    async def test_wrong_password_rejected(self, client, auth):
        response = await client.post(
            "/api/auth/login", json={"email": "lab@example.com", "password": "wrong password"}
        )
        assert response.status_code == 401

    async def test_unknown_email_rejected(self, client):
        response = await client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever pass"}
        )
        assert response.status_code == 401

    async def test_short_password_rejected(self, client):
        response = await client.post(
            "/api/auth/register", json={"email": "x@y.com", "password": "short"}
        )
        assert response.status_code == 422

    async def test_protected_routes_require_a_token(self, client):
        assert (await client.get("/api/samples")).status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_garbage_token_rejected(self, client):
        response = await client.get("/api/samples", headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401


class TestSamples:
    async def test_crud(self, client, auth):
        created = await client.post(
            "/api/samples",
            headers=auth,
            json={
                "name": "Batch A",
                "production_route": "liquid_phase_exfoliation",
                "feedstock": "flake graphite",
                "properties": {"bet_m2_g": 140.0, "flake_size_um": 9.0},
            },
        )
        assert created.status_code == 201, created.text
        sample = created.json()
        assert sample["properties"]["bet_m2_g"] == 140.0

        listed = await client.get("/api/samples", headers=auth)
        assert [s["id"] for s in listed.json()] == [sample["id"]]

        patched = await client.patch(
            f"/api/samples/{sample['id']}", headers=auth, json={"name": "Batch A rev 2"}
        )
        assert patched.json()["name"] == "Batch A rev 2"
        # A partial update must not wipe unrelated fields.
        assert patched.json()["properties"]["bet_m2_g"] == 140.0

        assert (await client.delete(f"/api/samples/{sample['id']}", headers=auth)).status_code == 204
        assert (await client.get(f"/api/samples/{sample['id']}", headers=auth)).status_code == 404

    async def test_malformed_id_is_400_not_500(self, client, auth):
        response = await client.get("/api/samples/not-an-objectid", headers=auth)
        assert response.status_code == 400

    async def test_samples_are_isolated_between_users(self, client, auth):
        mine = (await client.post("/api/samples", headers=auth, json={"name": "Mine"})).json()

        other = await client.post(
            "/api/auth/register", json={"email": "other@example.com", "password": "different password"}
        )
        other_auth = {"Authorization": f"Bearer {other.json()['access_token']}"}

        assert (await client.get("/api/samples", headers=other_auth)).json() == []
        assert (await client.get(f"/api/samples/{mine['id']}", headers=other_auth)).status_code == 404
        assert (
            await client.delete(f"/api/samples/{mine['id']}", headers=other_auth)
        ).status_code == 404

    async def test_invalid_production_route_rejected(self, client, auth):
        response = await client.post(
            "/api/samples", headers=auth, json={"name": "X", "production_route": "alchemy"}
        )
        assert response.status_code == 422

    async def test_negative_bet_rejected(self, client, auth):
        response = await client.post(
            "/api/samples", headers=auth, json={"name": "X", "properties": {"bet_m2_g": -5}}
        )
        assert response.status_code == 422


class TestUploads:
    @pytest_asyncio.fixture
    async def sample_id(self, client, auth):
        response = await client.post(
            "/api/samples",
            headers=auth,
            json={"name": "GNP batch", "properties": {"bet_m2_g": 140.0, "flake_size_um": 9.0}},
        )
        return response.json()["id"]

    async def test_raman_upload(self, client, auth, sample_id):
        response = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("wire.txt", gnp_raman_file(), "text/plain")},
            data={"technique": "raman"},
        )
        assert response.status_code == 201, response.text
        spectra = response.json()
        assert len(spectra) == 1
        meta = spectra[0]["meta"]
        assert meta["excitation_nm"] == 532.0
        assert meta["detected_format"] == "renishaw_wire_ascii"
        # The preview must be decimated but still bounded and non-trivial.
        assert 100 < len(spectra[0]["preview_x"]) <= 1200

    async def test_xps_upload_detects_regions(self, client, auth, sample_id):
        for name, payload in (("c1s.csv", c1s_file()), ("o1s.csv", o1s_file())):
            response = await client.post(
                f"/api/samples/{sample_id}/spectra",
                headers=auth,
                files={"file": (name, payload, "text/csv")},
                data={"technique": "xps"},
            )
            assert response.status_code == 201, response.text
        listed = (await client.get(f"/api/samples/{sample_id}/spectra", headers=auth)).json()
        assert {s["region"] for s in listed} == {"c1s", "o1s"}

    async def test_unparseable_upload_is_422(self, client, auth, sample_id):
        response = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("junk.csv", b"this is not a spectrum at all", "text/csv")},
            data={"technique": "raman"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]

    async def test_empty_upload_is_400(self, client, auth, sample_id):
        response = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("empty.csv", b"", "text/csv")},
            data={"technique": "raman"},
        )
        assert response.status_code == 400

    async def test_binary_instrument_file_gives_actionable_error(self, client, auth, sample_id):
        response = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("scan.wdf", b"\x00\x01\x02" * 400, "application/octet-stream")},
            data={"technique": "raman"},
        )
        assert response.status_code == 422
        assert "export to CSV" in response.json()["detail"]

    async def test_upload_to_other_users_sample_is_404(self, client, auth, sample_id):
        other = await client.post(
            "/api/auth/register", json={"email": "third@example.com", "password": "yet another pass"}
        )
        other_auth = {"Authorization": f"Bearer {other.json()['access_token']}"}
        response = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=other_auth,
            files={"file": ("wire.txt", gnp_raman_file(), "text/plain")},
            data={"technique": "raman"},
        )
        assert response.status_code == 404

    async def test_spec_sheet_merges_into_properties(self, client, auth, sample_id):
        response = await client.post(
            f"/api/samples/{sample_id}/spec",
            headers=auth,
            files={"file": ("spec.json", b'{"Youngs modulus": "0.9 TPa", "thickness": "3 nm"}', "application/json")},
        )
        assert response.status_code == 200, response.text
        properties = response.json()["properties"]
        assert properties["youngs_modulus_gpa"] == 900.0
        assert properties["thickness_nm"] == 3.0
        # Values set at creation time survive the merge.
        assert properties["bet_m2_g"] == 140.0

    async def test_spectrum_deletion(self, client, auth, sample_id):
        upload = await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("wire.txt", gnp_raman_file(), "text/plain")},
            data={"technique": "raman"},
        )
        spectrum_id = upload.json()[0]["id"]
        assert (
            await client.delete(f"/api/samples/{sample_id}/spectra/{spectrum_id}", headers=auth)
        ).status_code == 204
        assert (await client.get(f"/api/samples/{sample_id}/spectra", headers=auth)).json() == []


class TestAnalysis:
    @pytest_asyncio.fixture
    async def prepared(self, client, auth):
        sample_id = (
            await client.post(
                "/api/samples",
                headers=auth,
                json={
                    "name": "GNP batch",
                    "production_route": "liquid_phase_exfoliation",
                    "properties": {"bet_m2_g": 140.0, "flake_size_um": 9.0},
                },
            )
        ).json()["id"]
        await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("wire.txt", gnp_raman_file(), "text/plain")},
            data={"technique": "raman"},
        )
        for name, payload in (("c1s.csv", c1s_file()), ("o1s.csv", o1s_file())):
            await client.post(
                f"/api/samples/{sample_id}/spectra",
                headers=auth,
                files={"file": (name, payload, "text/csv")},
                data={"technique": "xps"},
            )
        return sample_id

    async def test_analysis_without_spectra_is_400(self, client, auth):
        sample_id = (await client.post("/api/samples", headers=auth, json={"name": "Bare"})).json()["id"]
        response = await client.post(f"/api/samples/{sample_id}/analyse", headers=auth)
        assert response.status_code == 400
        assert "Upload at least one" in response.json()["detail"]

    async def test_full_analysis(self, client, auth, prepared):
        response = await client.post(f"/api/samples/{prepared}/analyse", headers=auth)
        assert response.status_code == 201, response.text
        report = response.json()

        assert report["classification"]["form"] in {"multilayer_gnp", "few_layer"}
        assert report["classification"]["confidence"] > 0.3
        assert report["raman"]["id_ig"] == pytest.approx(0.28, rel=0.3)
        assert report["raman"]["excitation_nm"] == 532.0
        assert report["xps"]["co_ratio"] is not None
        assert len(report["applications"]) == 3
        assert len(report["peers"]) == 3
        assert report["scorecard"]
        assert report["rankings"]
        assert report["citations"]
        assert report["engine_version"]
        assert report["sample_name"] == "GNP batch"

    async def test_report_is_persisted_and_retrievable(self, client, auth, prepared):
        created = (await client.post(f"/api/samples/{prepared}/analyse", headers=auth)).json()

        by_sample = (await client.get(f"/api/samples/{prepared}/reports", headers=auth)).json()
        assert [r["id"] for r in by_sample] == [created["id"]]

        directly = await client.get(f"/api/reports/{created['id']}", headers=auth)
        assert directly.status_code == 200
        assert directly.json()["id"] == created["id"]

        all_reports = (await client.get("/api/reports", headers=auth)).json()
        assert created["id"] in [r["id"] for r in all_reports]

    async def test_history_accumulates(self, client, auth, prepared):
        await client.post(f"/api/samples/{prepared}/analyse", headers=auth)
        await client.post(f"/api/samples/{prepared}/analyse", headers=auth)
        reports = (await client.get(f"/api/samples/{prepared}/reports", headers=auth)).json()
        assert len(reports) == 2

    async def test_reports_are_isolated_between_users(self, client, auth, prepared):
        created = (await client.post(f"/api/samples/{prepared}/analyse", headers=auth)).json()
        other = await client.post(
            "/api/auth/register", json={"email": "fourth@example.com", "password": "one more password"}
        )
        other_auth = {"Authorization": f"Bearer {other.json()['access_token']}"}
        assert (await client.get(f"/api/reports/{created['id']}", headers=other_auth)).status_code == 404
        assert (await client.get("/api/reports", headers=other_auth)).json() == []

    async def test_raman_only_analysis_still_works(self, client, auth):
        sample_id = (
            await client.post("/api/samples", headers=auth, json={"name": "Raman only"})
        ).json()["id"]
        await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=auth,
            files={"file": ("wire.txt", gnp_raman_file(), "text/plain")},
            data={"technique": "raman"},
        )
        report = (await client.post(f"/api/samples/{sample_id}/analyse", headers=auth)).json()
        assert report["xps"] is None
        assert report["raman"]["id_ig"] is not None
        assert any("No XPS data" in w for w in report["warnings"])

    async def test_deleting_sample_removes_reports(self, client, auth, prepared):
        created = (await client.post(f"/api/samples/{prepared}/analyse", headers=auth)).json()
        await client.delete(f"/api/samples/{prepared}", headers=auth)
        assert (await client.get(f"/api/reports/{created['id']}", headers=auth)).status_code == 404
