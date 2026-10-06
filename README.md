# Graphene Benchmarker

Upload a graphene sample's Raman, XPS and property data. The app fits the spectra,
quantifies the surface chemistry, classifies the material form, benchmarks it against
open reference datasets, recommends applications, and finds the closest commercial
products from global producers.

```
backend/    FastAPI + numpy/scipy/lmfit analysis engines, MongoDB, JWT auth
frontend/   React + TypeScript + Vite + Tailwind + Plotly dashboard
```

## Quick start

On a fresh machine, one command does everything — virtualenv, dependencies,
`.env` with a generated `JWT_SECRET`, reference-data seeding, and both servers:

```bash
./run.sh            # dev: FastAPI + Vite with hot reload
./run.sh --prod     # build the SPA and serve it from FastAPI on a single port
```

It needs Python 3.11+, Node 20+, and a reachable MongoDB (it will start a
`mongo:7` container automatically if Docker is available and nothing is
listening on 27017). Point it elsewhere with `MONGODB_URI=... ./run.sh`; ports
are chosen automatically and can be pinned with `BACKEND_PORT` / `FRONTEND_PORT`.

The manual steps below are equivalent, if you prefer to run the pieces yourself.

### 1. Backend

```bash
cd backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then set MONGODB_URI and JWT_SECRET
./.venv/bin/python -m seed.seed          # load the reference library
./.venv/bin/uvicorn app.main:app --reload --port 8100
```

`MONGODB_URI` accepts an Atlas SRV string or a local `mongod`. Generate a signing key
with `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

A local MongoDB on macOS:

```bash
brew tap mongodb/brew && brew trust mongodb/brew
brew install mongodb-community
brew services start mongodb-community    # listens on localhost:27017
```

API docs are at `http://localhost:8100/docs`.

> **Port note:** `8000` and `8010` are already taken on this machine by unrelated
> services, which is why the commands above use `8100`. To find a free port:
> `python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"`

### 2. Frontend

```bash
cd frontend
npm install
VITE_API_TARGET=http://127.0.0.1:8100 npm run dev     # http://localhost:5173
```

The proxy defaults to `http://127.0.0.1:8000`, so pass `VITE_API_TARGET` whenever the
backend is on a different port. Vite binds IPv6 `localhost` only — use
`http://localhost:5173`, not `127.0.0.1`.

### No MongoDB handy?

A development server backed by an in-memory database, with a seeded demo account and a
completed analysis:

```bash
cd backend
./.venv/bin/python -m scripts.dev_mock_server --demo --port 8000
# demo@example.com / demo-password-123
```

Nothing persists across restarts. Development only.

## Application finder (TDS matching)

Type the numbers off your own carbon's technical data sheet, exactly as the sheet
quotes them (`20-50`, `<10`, `>99.5`, `~300`), and the finder ranks the applications
the market sells material like yours into. It lives at **Application finder** in the
header and at `POST /api/tds/match`.

The evidence is the *application database*: 39 commercial grades from 15 producers
(Hydrograph, First Graphene, NanoXplore, AdNano, Levidian, GTechPlasma, Matexcel,
The Sixth Element, ...), each with the applications its vendor sells it into and the
specs its datasheet quotes, plus a 160-grade market survey (MDPI *Carbon* 2026,
Tables S1/S2) used for percentile context.

```
backend/seed/data/source/            the survey workbook and the literature tables
backend/seed/data/tds_pdfs/          vendor TDS PDFs, served at /api/tds/datasheets/{file}
backend/seed/data/application_taxonomy.json   14 applications: keywords, description,
                                              parameter weights, preferred forms
backend/seed/data/application_products.json   generated: one record per grade
backend/seed/data/market_reference.json       generated: survey specs as intervals
backend/scripts/build_application_database.py the generator
backend/app/analysis/specvalue.py             "20-50" / "<10" / ">99" -> intervals
backend/app/analysis/tds_match.py             the matching engine
```

### How a match is scored

Every spec, yours and the vendors', is an interval. For each application:

1. **Evidence set** — the grades tagged with that application (vendor text mapped to
   the taxonomy by keyword; a few tags were read off the datasheets by hand).
2. **Market check per parameter** — how many of those grades quote a range that
   overlaps yours. Overlapping any grade passes, graded by the fraction that overlap;
   landing in a gap between grades is borderline; falling outside the market hull is
   borderline within one tolerance (0.3 decades for log-scale parameters such as
   size, layers, BET and conductivity; a fixed amount for purity, oxygen, I(D)/I(G))
   and a fail beyond it. An open vendor bound such as `>3500 S/m` is treated as that
   figure plus a tolerance, not as infinity, so it cannot match everything.
3. **Weighting** — parameters are weighted per application (BET dominates
   supercapacitors, lateral size dominates barrier coatings, conductivity dominates
   inks and EMI). Weights live in the taxonomy file and are the one hand-tuned input.
4. **Closest grade** — a weighted spec distance to each grade in the evidence set,
   normalised by how much the whole database varies in each parameter.
5. **Score** = 100 × (0.65 × market fit + 0.35 × closest-grade similarity), trimmed
   10 % when your form (powder / dispersion / paste / pellet) is not one the
   application ships in, and damped when fewer than four grades back the application.
   **Confidence** reports evidence count and how many weighted parameters you supplied.

The response carries the per-parameter checks, the strengths and gaps in plain
English, the three closest grades sold for each use with links to their TDS, the five
closest grades overall, and where your values sit as percentiles of the wider market.
Matches are saved per user (`GET /api/tds/matches`).

**Your own TDS is the form.** `backend/seed/data/my_tds.json` holds the Faraday Earth 8X1
sheet as transcribed (values, test methods, descriptive rows); `GET /api/tds/template`
serves it and the finder opens prefilled with it. Edit any value in the portal and the
ranking re-computes as you type through `POST /api/tds/preview` (no save); each
application shows the change in score and rank against the sheet as filed, so you can
see which parameters move which applications. Press **Save this match** to keep a
snapshot. When the sheet is reissued, update `my_tds.json`.

### Updating the database

Edit the workbook in `backend/seed/data/source/`, drop new PDFs into `tds_pdfs/`, then:

```bash
cd backend
./.venv/bin/python -m scripts.build_application_database   # regenerate the JSON
./.venv/bin/python -m seed.seed                              # upsert into Mongo
```

The generator recovers ranges that Excel turned into dates (`2-3` layers stored as
2026-02-03), and applies the overrides in its `CORRECTIONS` table where a datasheet
contradicted the sheet (for example RGA-COOH-1's lateral size is 20-50 **nm**, not
µm). Every override is stored on the product record under `corrections` with its
reason. Unit conventions: lateral size µm, BET m²/g, conductivity S/m, bulk density
g/cm³, oxygen and purity in %.

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest         # ~4 min; real curve fits
```

- `tests/test_parsers.py` — delimiter/encoding sniffing, vendor headers, VAMAS, spec sheets
- `tests/test_analysis.py` — engines against synthetic spectra with known ground truth
- `tests/test_api.py` — the full HTTP surface against an in-memory MongoDB

```bash
cd frontend && npm run build                      # typecheck + production build
```

## What the analysis actually does

### Raman

1. **Baseline** — arPLS (Baek et al., *Analyst* 140, 250), with the smoothing parameter
   derived from the sampling interval so the result does not depend on how densely the
   instrument sampled the spectrum.
2. **Peak fitting** — pseudo-Voigt for D, G, D′, D3, 2D and D+D′. Optional bands are
   admitted only when they lower AIC and clear the noise floor; a band fitted below
   4σ is either rejected (2D, optional bands) or kept and flagged as an upper limit
   (D, G), because a near-absent D band is the headline result for pristine material.
3. **Metrics** — I(D)/I(G), I(2D)/I(G), FWHM(2D), crystallite size *L*ₐ and defect
   density *n*_D via Cançado (*APL* 88, 163106 and *Nano Lett.* 11, 3190). Both scale as
   λ⁴, so the excitation wavelength is read from the file header or set explicitly. The
   defect metrics are suppressed above I(D)/I(G) ≈ 1, past the Tuinstra–Koenig maximum
   where the relations become double-valued.
4. **Layer count** — from 2D width and I(2D)/I(G), with band *shape* overriding width
   for AB-stacked bilayers (a four-component 2D envelope is as wide as few-layer
   material). A 2D band broader than 95 cm⁻¹ is the quenched remnant seen in GO/rGO and
   yields no layer count at all. Single-Lorentzian 2D is detected by comparing residual
   sums of squares against a four-component fit, which is robust to baseline error in a
   way an R² or AIC comparison is not.

### XPS

1. **Charge referencing** — anchored to the *lowest*-binding-energy carbon maximum, not
   the tallest peak: in heavily oxidised material the C–O component outgrows the
   graphitic line and would drag the energy scale ~2 eV off.
2. **Background** — iterative Shirley.
3. **Deconvolution** — asymmetric sp² plus sp³, C–O, C=O, O–C=O and the π–π\* shake-up,
   sharing one line width. The sp² asymmetry is held fixed at an HOPG-calibrated value;
   letting it float makes the sp²/sp³ split arbitrary, since an unconstrained tail is
   nearly degenerate with an sp³ component 0.7 eV away.
4. **Quantification** — Scofield cross-sections with a KE^0.6 transmission correction.
   Validated to ~2% on synthetic data. The sp²/sp³ split carries roughly ±10 percentage
   points depending on the true lineshape, and the report says so.

### Classification, Module A, Module B

- Fuzzy rule scoring over the available Raman + XPS metrics, normalised by the evidence
  actually present so a Raman-only sample stays comparable. Confidence folds in the
  margin over the runner-up, so a near-tie never reads as certain.
- Applications are scored per criterion with graded margins (a comfortable pass outranks
  a marginal one), and the verdict comes from the criteria themselves rather than a
  threshold on the score. Mismatches are stated in plain language.
- Peer matching uses a weighted Gower-style distance, log-scaled for span-heavy features
  and normalised by how much the catalogue actually varies in each, with a penalty for
  a different physical form rather than a hard filter.

## Reference library

`backend/seed/data/` holds curated JSON: 11 Raman and 6 XPS reference points, 8 DFT and
experimental property records, 7 applications, and 44 commercial products from 25
producers. Every record carries source, DOI or URL, version and retrieval date, and the
report cites what it used.

Overlay traces are **synthesised from published peak parameters**, not raw dataset files —
the published parameters are what the literature reports. The UI states this. Drop real
dataset files in and extend the seed script to ingest them when you want true traces.

Producer specs are **nominal grade values compiled from public datasheets, not batch
certificates**. Values not quoted on a datasheet (usually I(D)/I(G) and C/O) are
representative estimates for the product class. Confirm current-batch specs with the
vendor.

## Not built yet

Phase 6 items: PDF export (WeasyPrint/ReportLab), and batch-comparison views across
samples. Analysis history is implemented — each run is stored and selectable per sample.
Instrument-native binary formats (Renishaw `.wdf`, Bruker `.opus`) are rejected with a
message telling the user to export to CSV.
