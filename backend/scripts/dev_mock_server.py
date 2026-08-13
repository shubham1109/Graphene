"""Run the API against an in-memory MongoDB, for local UI work without infra.

DEVELOPMENT ONLY. Nothing persists: every restart begins from a freshly seeded
reference library and no user accounts. Use `uvicorn app.main:app` with a real
MONGODB_URI for anything you care about keeping.

    python -m scripts.dev_mock_server [--port 8000] [--demo]

--demo also creates a demo account (demo@example.com / demo-password-123) with a
sample, synthetic Raman and XPS spectra, and one completed analysis, so the
dashboard has something to show immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

import numpy as np
import uvicorn
from mongomock_motor import AsyncMongoMockClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module  # noqa: E402
from app.db import ensure_indexes  # noqa: E402
from seed.seed import seed  # noqa: E402

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-123"


def _lorentzian(x, centre, height, fwhm):
    return height / (1.0 + ((x - centre) / (fwhm / 2.0)) ** 2)


def _gaussian_area(x, centre, area, fwhm):
    sigma = fwhm / 2.35482
    return area / (sigma * np.sqrt(2 * np.pi)) * np.exp(-((x - centre) ** 2) / (2 * sigma**2))


def _csv(x, y, header: str = "", delimiter: str = ",") -> bytes:
    body = "\n".join(f"{a:.5f}{delimiter}{b:.5f}" for a, b in zip(x, y))
    return ((header + "\n" if header else "") + body).encode()


def demo_files() -> dict[str, bytes]:
    """Synthetic few-layer GNP Raman plus C 1s and O 1s XPS regions."""
    rng = np.random.default_rng(3)

    x = np.linspace(1000.0, 3200.0, 2400)
    raman = (
        _lorentzian(x, 1350.0, 280.0, 50.0)
        + _lorentzian(x, 1582.0, 1000.0, 24.0)
        + _lorentzian(x, 1620.0, 100.0, 24.0)
        + _lorentzian(x, 2712.0, 380.0, 76.0)
        + 80.0
        + 0.01 * x
        + rng.normal(0.0, 4.0, x.size)
    )

    from lmfit.lineshapes import skewed_voigt

    cx = np.linspace(281.0, 293.0, 900)
    sp2 = skewed_voigt(cx, amplitude=1.0, center=284.5, sigma=0.42, gamma=0.12, skew=0.45)
    sp2 = sp2 / np.trapezoid(sp2, cx)
    c1s = (
        7000.0 * sp2
        + _gaussian_area(cx, 285.3, 700.0, 1.1)
        + _gaussian_area(cx, 286.6, 400.0, 1.1)
        + _gaussian_area(cx, 290.8, 350.0, 1.9)
        + 300.0
        + 40.0 * (cx - cx[0]) / (cx[-1] - cx[0])
        + rng.normal(0.0, 20.0, cx.size)
    )

    ox = np.linspace(527.0, 540.0, 600)
    o1s = (
        _gaussian_area(ox, 531.2, 120.0, 1.6)
        + _gaussian_area(ox, 532.7, 260.0, 1.6)
        + 250.0
        + 30.0 * (ox - ox[0]) / (ox[-1] - ox[0])
        + rng.normal(0.0, 20.0, ox.size)
    )

    return {
        "demo_raman.txt": _csv(
            x, raman, "#Renishaw WiRE ASCII export\n#Laser: 532 nm\n#Wave\t#Intensity", "\t"
        ),
        "demo_c1s.csv": _csv(cx, c1s, "Binding Energy (eV),Counts"),
        "demo_o1s.csv": _csv(ox, o1s, "Binding Energy (eV),Counts"),
    }


async def build_demo(database) -> None:
    """Create the demo account through the real API so nothing is faked."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://local") as client:
        registration = await client.post(
            "/api/auth/register",
            json={
                "email": DEMO_EMAIL,
                "password": DEMO_PASSWORD,
                "full_name": "Demo Lab",
                "organisation": "Graphene Benchmarker demo",
            },
        )
        if registration.status_code != 201:
            print(f"  demo account already present ({registration.status_code})")
            return
        headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

        sample = await client.post(
            "/api/samples",
            headers=headers,
            json={
                "name": "LPE batch 2026-08-A",
                "production_route": "liquid_phase_exfoliation",
                "feedstock": "flake graphite",
                "batch_id": "A-0042",
                "properties": {"bet_m2_g": 140.0, "flake_size_um": 9.0, "youngs_modulus_gpa": 900.0},
            },
        )
        sample_id = sample.json()["id"]

        files = demo_files()
        await client.post(
            f"/api/samples/{sample_id}/spectra",
            headers=headers,
            files={"file": ("demo_raman.txt", io.BytesIO(files["demo_raman.txt"]), "text/plain")},
            data={"technique": "raman"},
        )
        for name in ("demo_c1s.csv", "demo_o1s.csv"):
            await client.post(
                f"/api/samples/{sample_id}/spectra",
                headers=headers,
                files={"file": (name, io.BytesIO(files[name]), "text/csv")},
                data={"technique": "xps"},
            )

        report = await client.post(f"/api/samples/{sample_id}/analyse", headers=headers)
        if report.status_code == 201:
            classification = report.json()["classification"]
            print(f"  demo analysis: {classification['label']} (confidence {classification['confidence']})")
        else:
            print(f"  demo analysis failed: {report.status_code} {report.text[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--demo", action="store_true", help="Seed a demo account and analysis")
    args = parser.parse_args()

    database = AsyncMongoMockClient()["graphene_dev"]

    # Replace the module-level handle and neutralise real connections, so the
    # app's lifespan does not reach for a MongoDB that is not there.
    db_module._db = database

    async def _connect():
        await ensure_indexes(database)
        return database

    async def _disconnect():
        return None

    db_module.connect = _connect  # type: ignore[assignment]
    db_module.disconnect = _disconnect  # type: ignore[assignment]

    async def prepare():
        await ensure_indexes(database)
        summary = await seed(database)
        for collection, (inserted, updated) in summary.items():
            print(f"  {collection:24s} {inserted:3d} inserted, {updated:3d} updated")
        if args.demo:
            await build_demo(database)

    print("Starting DEVELOPMENT server with an in-memory database (nothing persists).")
    asyncio.run(prepare())
    if args.demo:
        print(f"\n  Demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}\n")

    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
