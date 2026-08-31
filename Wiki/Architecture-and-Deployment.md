# Architecture and Deployment

## Pipeline

```
fetch  →  build  →  dashboard  →  (health gate)  →  deploy
```

`ons_pipeline.py` is the CLI for the first three steps plus `health` and
`verify`; `dashboard.py` (imported by it) generates the HTML. Nothing here
needs a database or a server — the output is one static file.

**fetch** downloads ONS's `.parquet` resources (falling back to `.csv` with
delimiter/decimal sniffing for years before ONS published parquet) into
`raw/`, comparing `Content-Length` against the local copy so a normal daily
run only re-downloads the current month's files.

**build** aggregates `raw/` into `data/daily.parquet` (+ `.csv`), classifying
plant-level fuel types, joining installed capacity, and running the
data-integrity tripwires described below. Per-file aggregates are cached
under `data/_cache/`, keyed by a version tag bumped whenever the aggregation
logic changes (forcing a clean re-aggregate rather than silently reusing
stale cache entries).

**dashboard** reads the store and writes the single HTML file — the JSON
payload is gzip-compressed and base64-embedded, inflated client-side via
`DecompressionStream` (requires Chrome/Edge 80+, Firefox 113+, or Safari
16.4+; older browsers get a message instead of a blank page). `SERIES_META`
in `dashboard.py` (Python) is the single source of truth that drives both
what gets aggregated and what the JS UI can chart.

### Data sources

| Dataset | ONS `dados.ons.org.br` slug | Granularity |
|---|---|---|
| Balanço de Energia nos Subsistemas | `balanco-energia-subsistema` | hourly |
| Geração Térmica por Motivo de Despacho | `geracao-termica-despacho-2` | hourly, per plant |
| Capacidade Instalada de Geração | `capacidade-geracao` | current snapshot, per generating unit |
| Dados Hidrológicos por Reservatório | `dados-hidrologicos-res` | daily, per reservoir |
| EAR Diário por Subsistema | `ear-diario-por-subsistema` | daily |
| EAR Diário por REE | (S3 dir `ear_ree_di`) | daily, per REE |
| ENA Diário por Subsistema | `ena-diario-por-subsistema` | daily |
| CMO Semi-Horário | `cmo-semi-horario` | 30-minute |

The public dataset-page slugs above do **not** reliably match the internal
S3 folder names (`s3_dir` in `ons_pipeline.py`'s `Source` config) — always
confirm a dataset's real landing-page URL directly on `dados.ons.org.br`
rather than deriving it from the S3 slug.

### Capacity, utilization & gas consumption

`geracao-termica-despacho-2` reports by dispatch *phase*, not physical
plant — a combined-cycle block can dispatch as several separately-named
phases sharing one CEG (ANEEL's venture ID). Capacity comes from a separate
dataset (`capacidade-geracao`, one row per generating unit) and is joined by
CEG, not by name. A CEG with multiple phases gets one synthesized combined
entity (summed generation, real total capacity); the original phase entities
are left with their own generation but no capacity/utilization/gas figure of
their own, so nothing is double-counted or overstated at the phase level.
Estimated gas consumption applies a heat-rate assumption ONS does not
publish per plant — see [Known Limitations](Known-Limitations-and-Assumptions)
for the exact figures and why they're an assumption, not a sourced number.

## Single-file HTML

Everything the page needs — data, fonts, styling — is embedded, so it works
from a `file://` path, as an email attachment, or from any static host with
no build step at request time. `fonts/Degular.ttf` is read and base64-embedded
as a `@font-face` at build time (falls back to the system font stack if the
file is ever missing from a checkout).

## CI/CD (`.github/workflows/refresh.yml`)

Runs on a daily cron (`40 20 * * *` UTC — after ONS's second daily publish
at 19:00 UTC), on push to `main` when the pipeline files change, and on
manual dispatch (including the dashboard's own "Refresh data" button, below).

```
checkout → setup Python 3.12 → pip install →
restore raw-file cache (keyed by calendar month) →
verify → fetch → build →
health gate →
build the page (site/index.html) →
[mirror publishes — see below] →
upload Pages artifact → stamp last-refresh commit
```

**Health gate**: runs `ons_pipeline.py health --max-age-days 5 --min-rows
100000 --min-series 15 --min-plants 50 --min-reservoirs 50` between build and
deploy. A failed gate blocks the deploy entirely — yesterday's working
dashboard stays live rather than being replaced with a broken one.

**Keep-alive stamp**: a scheduled GitHub Actions workflow disables itself
after 60 days with no repository activity. The final step commits
`.state/last_refresh.json` every run (tagged `[skip ci]` so it doesn't
trigger another run) — this is what keeps the daily cron alive, and doubles
as a visible record of when the data last refreshed.

**Data-integrity tripwires**, printed to the build log every run:

- **Interchange sign**: `normalize_balance` reconciles `Load = Production −
  Interchange` per calendar year (not once across the whole history — ONS
  has changed its reporting sign convention mid-window before), logging the
  residual for each year.
- **Fuel splits vs. balance thermal**: the per-fuel numbers come from the
  thermal dispatch file; `gen_thermal` comes from the balance file — two
  separate ONS publications. Every build logs the median/p95 gap between
  them and flags anything above 3%.
- **Ambiguous fuel labels**: an ONS fuel string containing "gas" that isn't
  an exact match in `GAS_FUELS` prints a one-time warning naming the exact
  raw label and falls into "Thermal — other" rather than being guessed as
  gas — see [Known Limitations](Known-Limitations-and-Assumptions).

## Multi-site hosting topology

Three properties, one `gasbrazil` account used purely as a firewall-friendly
hub — some corporate DNS/proxy filters flag a newly-registered custom domain
like `gasbrazil.com` (or its subdomains) as suspicious regardless of content,
where a plain `*.github.io` URL has no such reputation problem:

| Repo | Primary URL | Mirror(s) |
|---|---|---|
| `caissonpoint/ons-dashboard` (this repo) | `ons.gasbrazil.com` | `ons-dashboard.github.io`, `gasbrazil.github.io/ons` |
| `caissonpoint/poc-dashboard` | `poc.gasbrazil.com` | `caissonpoint.github.io/poc-dashboard`, `gasbrazil.github.io/poc` |
| `caissonpoint/gasbrazil-com` | `gasbrazil.com` | `gasbrazil.github.io` |

Each mirror is a separate GitHub Pages repo, classic branch-based (not
Actions-build) since it only ever receives one already-built `index.html`.
`refresh.yml`'s mirror steps clone the mirror repo, copy `site/index.html`
in, commit, and push — using an SSH deploy key scoped to that one repo only
(GitHub doesn't allow reusing one SSH key across multiple repos, so each
mirror has its own keypair). Every mirror step is `continue-on-error: true`
and gated on its secret being set, so a mirror failure can never break the
real build or deploy. The dashboard's own header links (see
[Using the Dashboard](Using-the-Dashboard)) resolve to the right sibling URL
based on `location.hostname` at load time — `SITE_LINKS`/`siteFlavor()` in
`dashboard.py`.

The landing page (`gasbrazil-com` → `gasbrazil.github.io`) is a one-time
snapshot, not on its own sync schedule — it changes rarely, so there's no CI
step for it currently.

## "Refresh data" button infrastructure

The dashboard is a public static file with no backend, so triggering
`refresh.yml`'s `workflow_dispatch` on demand needs an authenticated call to
GitHub's API — and a token that can do that must never live in the page's
own client-side JS. A small Cloudflare Worker sits in between: it validates
the request `Origin` against the known dashboard hostnames, applies a
lightweight cooldown, and calls GitHub's dispatch endpoint using a
`GITHUB_TOKEN` held as an encrypted Cloudflare Worker secret (never in the
Worker's own script, never in the page). The dashboard's `REFRESH_WORKER_URL`
constant points at the deployed Worker.

## Local development

- `make_mock.py` generates a full synthetic raw-data tree (including mock
  capacity/CEG and multi-phase combined-cycle data) so the whole pipeline can
  be exercised offline, without ONS access.
- `requirements.txt` deliberately upper-bounds `pandas<3.1` — a routine CI
  run once picked up pandas 3.0 the day it released, which turns every text
  metric column into `str` dtype and breaks `groupby().mean()` with no
  commit behind the failure. Keep that upper bound when bumping dependencies.
- The standing local-dev limitation: an older local Python/pandas
  installation can't run the pinned pandas version, so end-to-end
  verification (mock rebuild + Playwright regression pass) may need to run
  in a separate environment with the pinned versions installed rather than
  wherever the repo is checked out day to day.
