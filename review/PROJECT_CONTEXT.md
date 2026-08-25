# ONS Balances Dashboard — project context for review

## What this is

A personal side project (owner: Eric Brooks, manager of Americas natural gas pricing at
S&P Global Commodity Insights) that scrapes Brazilian grid operator (ONS) open data,
aggregates it into a daily store, and generates a single self-contained HTML dashboard.
Audience is implicitly gas-market participants: the dashboard's whole point is reading
Brazilian power-sector data (hydro, thermal generation, prices) through a lens of "what
does this mean for gas burn / gas-fired dispatch," since hydro conditions are the main
driver of Brazilian gas demand.

No company affiliation in the code or deployment — this is a personal GitHub repo
(`caissonpoint/ons-dashboard`), deployed to public GitHub Pages, built and maintained
solo with AI assistance (Claude, and now getting a second opinion from Gemini).

## Architecture at a glance

- **`ons_pipeline.py`** — CLI (`verify | fetch | build | dashboard | health | refresh`).
  Downloads ONS open-data files (parquet/CSV), aggregates hourly/semi-hourly sources to
  daily means, derives national (SIN) rows by summing subsystem rows, normalizes the
  net-interchange sign convention, classifies per-plant thermal generation by fuel type,
  and writes `data/daily.parquet` + `data/entities.parquet`.
- **`dashboard.py`** — generates one self-contained HTML file. ~1,300 lines total; the
  bulk of it is a Python triple-quoted string (`TEMPLATE`) containing the *entire*
  HTML/CSS/JS for the dashboard — no build tooling, no bundler, no JS framework, vanilla
  DOM manipulation, hand-rolled SVG charting. The data payload is JSON, gzip-compressed,
  base64-embedded in a `<script type="application/octet-stream">` tag, and inflated
  client-side via `DecompressionStream` (browser support requirement: Chrome/Edge 80+,
  Firefox 113+, Safari 16.4+).
- **`.github/workflows/refresh.yml`** — scheduled (20:40 UTC daily) + push-triggered CI:
  `verify → fetch → build → health gate → deploy` to GitHub Pages. The health gate
  (`ons_pipeline.py health`) blocks a deploy if the store is stale, too small, or has
  lost plants/reservoirs — a bad run leaves yesterday's working dashboard live rather
  than replacing it with a broken one.
- **Deployment**: fully static, one HTML file, no server/database/API call at runtime.
  Free GitHub Pages hosting; ~0.8 MB gzipped payload per visit.

## Data model

- **`SERIES_META`** (top of `dashboard.py`) is the single source of truth: a dict
  mapping metric key → `(label, unit, picker group, sums-across-subsystems, entity
  kind)`. It drives both the Python-side payload construction and the JS-side UI
  (grouping, labels, which metrics apply to which tab).
- Two "shapes" of series:
  - **Subsystem-level** (`entity=""`): `load`, `gen_hydro`, `gen_thermal`,
    `thermal_gas`/`thermal_coal`/`thermal_oil`/`thermal_nuclear`/`thermal_biomass`,
    `ear_pct`, `ena_pct_mlt`, `cmo`, etc. — one row per (date, subsystem).
  - **Per-entity** (`kind="plant"` or `"reservoir"`): `plant_verif`, `plant_prog`,
    `res_volutil_pct`, `res_level_m` — one row per (date, subsystem, entity).
- **SIN (national) rows are derived, not published by ONS.** `add_sin()` sums
  summable subsystem series; `ear_pct`/`ena_pct_mlt` are rebuilt from their components
  rather than averaged; CMO is an unweighted mean across the four subsystems.
- **Two more derived series added this session**: `gen_gas` (an exact alias of
  `thermal_gas`, just re-grouped) and `thermal_nongas` (`gen_thermal − thermal_gas`,
  clipped at 0) — added so gas sits as a first-class peer of hydro/wind/solar in the
  main picker group instead of being buried inside the fuel-detail breakdown.

## Recent changes (this session, 2026-08-24) — four passes

1. **Thermal Plants tab**: region filter, full sortable plant table, removed an old
   hard cap of 8 selectable series.
2. **Subsystems tab, gas-market lens**: promoted gas out of "Thermal by fuel" into the
   main "Balance" picker group; changed default smoothing from 7-day moving average to
   daily; added a KPI strip at the top (latest data date w/ staleness note, SIN load,
   gas generation MW/% of total/7-day trend, EAR% w/ 30-day change, CMO, largest net
   importer/exporter with correct sign handling); changed default chart selections;
   switched body font to Motiva Sans via an Adobe Fonts (Typekit) `<link>` in `<head>`
   (Eric has a license), with system-font fallback for anyone without it.
3. **Bug fix + per-region fuel mix**: `classify_fuel()` in `ons_pipeline.py` only
   recognized a short hardcoded list of gas-fuel strings (`"gas natural"`, `"gnl"`,
   `"lng"`, `"gas de processo"`) and silently dumped anything else into
   `thermal_other` — this is the likely reason gas was disappearing from "Thermal by
   fuel" on real data. Broadened the match list (folded in a previously-unused
   `GAS_FUELS` constant that already existed in the file but was never referenced) and
   added a catch-all: any fuel string still containing the substring `"gas"` after the
   nuclear/oil/coal/biomass checks now classifies as gas rather than "other." Also
   generalized the KPI generation-mix bar from a SIN-only Hydro/Gas/Other-thermal/
   Wind/Solar summary into a full 8-fuel breakdown (Hydro/Gas/Coal/Oil-diesel/
   Nuclear/Biomass/Wind/Solar, each summing to `production_total`) that follows
   whichever subsystem(s) are toggled in the existing multi-select "Subsystems"
   control (SIN and/or any combination of SE/S/NE/N).
4. **Reservoirs tab**: added a regional EAR (stored energy) summary table — one row
   per SIN/SE/S/NE/N with a capacity-filled bar, stored/capacity in MWmês, 30-day
   change, and inflow (ENA % of long-term average) — color-banded red/amber/green by
   fill level. Added a basin-level usable-volume rollup table, computed client-side
   from the existing per-reservoir data (no pipeline changes), grouped by the
   reservoir's basin, sorted lowest-first. Added highlight KPI tiles: most-stressed
   region, lowest individual reservoir, count of reservoirs below 20%. The original
   per-reservoir picker (region/basin/search filters, full entity table, charts) is
   unchanged, just relegated below the new summary.

**Separately**: `refresh.yml`'s GitHub Actions pins (`checkout@v4`→`v7`,
`setup-python@v5`→`v6`, `cache@v4`→`v5`) were identified as needing a bump to clear a
Node.js 20 deprecation warning in the Actions logs, but that specific edit could not be
written back automatically (workflow files are protected from remote/automated edits in
this environment) and has **not yet been manually applied** — the attached `refresh.yml`
is still on the old pins.

## Testing performed

Everything above was verified with ad-hoc Playwright scripts (written and run, then
discarded — not committed to the repo) against a locally rebuilt dashboard using
synthetic ONS-shaped mock data (`make_mock.py`), checking for JS console/page errors,
correct computed values, light/dark theme rendering, and non-regression across tabs.
**This has never been run against real ONS data** — no network path to
`dados.ons.org.br` was available in the environment doing this work. There is no
repo-committed automated test suite; the only runtime check is the `health` command,
which validates row counts/staleness/entity counts, not the correctness of individual
computations (fuel classification, sign conventions, KPI math, etc.).

## Known open questions / things worth a second opinion on

- **The `classify_fuel` catch-all fallback is unverified against real data.** The mock
  data's fuel strings already matched the *old* explicit list, so testing never
  exercised the new fallback path. Is there a real ONS fuel-type string containing the
  substring "gas" that should *not* count as natural gas (a dual-fuel plant label, "gás
  residual de refino" counted differently, etc.)? The fallback is deliberately biased
  toward over-including ambiguous strings as gas — is that the right call, or too
  aggressive?
- **No automated regression tests are committed.** Is that an acceptable risk for a
  project this size, or is there a cheap, high-value test worth adding (e.g., a
  snapshot/golden-file test that runs `make_mock.py` → `build` → asserts specific
  computed values)?
- **Single-file-with-embedded-template architecture.** `dashboard.py`'s `TEMPLATE` is a
  ~700-line Python string containing raw HTML/CSS/JS, with no linting, type-checking,
  or IDE support on the JS — every check this session was done by extracting the string
  and running `node --check` plus a headless browser. Reasonable tradeoff for a project
  this size, or worth restructuring (e.g., a separate `.html.j2`/`.js` file read at
  build time)?
- **Color-only status banding.** The reservoir-stress bands (red/amber/green) have no
  non-color redundant indicator — an accessibility gap for colorblind users.
- **EAR%/ENA%MLT as the primary regional hydro metric**, rather than a naive average of
  individual reservoir volume %, per ONS convention (reservoirs differ in generating
  potential per unit of water, so raw volume isn't directly comparable/summable) — does
  this reasoning actually hold, and is it correctly implemented?
- **Security/CI**: `refresh.yml` grants `contents: write`, `pages: write`,
  `id-token: write`. Standard for this deploy pattern, but worth a review pass — plus
  the newly added Typekit external stylesheet link, which is the first external-origin
  dependency in an otherwise fully self-contained, offline-capable page.

## Repo layout

```
ons_pipeline.py     downloader + aggregator + CLI (attached)
dashboard.py        HTML/JS dashboard generator, imported by the CLI (attached)
.github/workflows/refresh.yml   daily rebuild + GitHub Pages deploy (attached)
README.md           existing user-facing docs: data sources, setup, caveats (attached)
check_bulletin.py   reconciles the store against an official ONS DIARIO_*.xlsx bulletin
make_mock.py        generates ONS-shaped synthetic data for offline testing
requirements.txt
raw/                downloaded source files (gitignored)
data/                daily.parquet, daily.csv, entities.parquet, aggregate cache (gitignored)
ons_dashboard.html   the deliverable (gitignored — built by CI, not committed)
```

`README.md` (attached) already documents the ONS data sources, the bulletin-reconciliation
logic, unit conventions (MWmed vs. MWmês), and several caveats worth reading before
reviewing the code — it's genuinely useful context, not just user-facing fluff.
