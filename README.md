# ONS Balances — scraper + dashboard

Pulls Brazilian grid data from the ONS open-data portal, aggregates it to a daily
store, and generates a single self-contained HTML dashboard you open in a browser.

## What it collects

Every series below is the open-data publication of something the Boletim Diário
da Operação also prints, so the dashboard reproduces the bulletin rather than
approximating it. The `check_bulletin.py` script proves that for any given day.

| Dataset | ONS source | Native granularity | Bulletin equivalent |
|---|---|---|---|
| Balanço de Energia nos Subsistemas | `balanco_energia_subsistema_ho` | hourly | **Sheets 03–07, "Dados Diários acumulados"** — production total, hydro, thermal, wind, solar, interchange, load (MWmed) |
| Geração Térmica por Motivo de Despacho | `geracao_termica_despacho_2_ho` | hourly, per plant | **Sheet 09, "Produção Térmica"** — programmed vs verified MWmed per plant, and the source of the thermal-by-fuel splits |
| Dados Hidráulicos por Reservatório | `dados_hidrologicos_di` | daily, per reservoir | **Sheets 23–26, "Sit. Princ. Reservatórios"** — upstream level (m) and usable volume (%) |
| ENA Diário por Subsistema | `ena_subsistema_di` | daily | Sheet 21 — natural inflow energy, MWmês and % of MLT |
| EAR Diário por Subsistema | `ear_subsistema_di` | daily | Sheet 19/20 — reservoir storage, MWmês and % of capacity |
| CMO Semi-Horário | `cmo_tm` | 30-minute | Marginal operating cost, R$/MWh |
| Geração por Usina em Base Horária | `geracao_usina_2_ho` | hourly, per plant | *Optional.* Fuel splits across the full plant universe. Not downloaded by default — it is ~1 GB and `termica` already covers sheet 09's universe. |

Everything is by subsystem (SE/CO, S, NE, N) plus a derived SIN national row.
Hourly and semi-hourly sources are averaged to daily means.

Two things the build reconciles rather than assumes:

- **Net interchange sign.** Sheets 03–07 satisfy `Carga = Produção total −
  Intercâmbio`, so a positive interchange is a net export. The build detects
  which orientation ONS's `val_intercambio` is using and normalizes to the
  bulletin's, printing the residual so you can see the identity holds.
- **Fuel splits vs. balance thermal.** The per-fuel numbers come from the thermal
  dispatch file; `gen_thermal` comes from the balance file. They are separate ONS
  publications, so every build prints the gap between them and flags a median
  above 3%.

The pipeline reads the portal's `.parquet` resources where ONS publishes them
(2021 onward) and falls back to `.csv` with delimiter and decimal-separator
sniffing for older years — ONS has shipped both `;`/decimal-comma and
`,`/decimal-point CSVs over time.

## Setup

*macOS / Linux:*

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

*Windows (PowerShell):*

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Behind a corporate proxy, `requests` honors the standard environment variables:

```bash
export HTTPS_PROXY=http://proxy.host:port
```

## Use

```bash
python ons_pipeline.py verify      # confirm every source URL is reachable — run this first
python ons_pipeline.py refresh     # fetch + build + dashboard (the normal daily run)
open ons_dashboard.html
```

The individual steps, if you want them separately:

```bash
python ons_pipeline.py fetch       # download raw files into ./raw
python ons_pipeline.py build       # aggregate ./raw -> ./data/daily.parquet + daily.csv
python ons_pipeline.py dashboard   # write ons_dashboard.html from the daily store
python ons_pipeline.py health      # is the store fresh and complete? (CI deploy gate)
```

Useful flags:

```
--years 2019 2026        inclusive year range (default: the last 5 years)
--datasets balanco termica  restrict to a subset
--force                  re-download even when the local copy matches the remote size
--raw DIR --out DIR --html FILE
```

### Checking against a bulletin

Download any `DIARIO_dd-mm-yyyy.xlsx` from the
[Boletim Diário da Operação](https://sdro.ons.org.br/SDRO/DIARIO/index.htm) and
reconcile it against the store:

```bash
python check_bulletin.py DIARIO_20-08-2026.xlsx
```

It parses sheets 03–07, 09 and 23–26, matches every value to the store by date,
subsystem, plant or reservoir name and series, and reports what is missing and
what differs. Small residuals are normal when ONS has revised one publication and
not the other; large or systematic gaps are not.

### First run vs. daily refresh

The first `fetch` downloads a few hundred MB, mostly the monthly thermal dispatch
files. After that, `fetch` compares `Content-Length` against the local copy and
re-downloads only what changed, and `build` reuses cached per-file aggregates in
`data/_cache/`, so a daily refresh touches only the current month. Adding
`--datasets geracao` pulls the ~1 GB full-plant generation set as well; you do not
need it for anything in the bulletin.

To automate it, run `python ons_pipeline.py refresh` from cron or Task Scheduler
after ONS's second daily publish (19:00 UTC).

## The dashboard

`ons_dashboard.html` is one file with the data embedded — no server, no CDN, no
network calls. It works from a file:// path and survives being emailed. The data
is embedded gzipped and inflated in the browser via `DecompressionStream`, which
keeps a few million data points down to a file of a few MB. That needs Chrome or
Edge 80+, Firefox 113+, or Safari 16.4+; on anything older the page says so
instead of rendering blank.

Three tabs:

- **Subsystems** — the sheet 03–07 balance series, thermal by fuel, hydrology and CMO.
- **Thermal plants** — sheet 09. Every dispatched thermal plant, filterable by
  fuel and searchable by name, charted as verified, programmed, or deviation %.
- **Reservoirs** — sheets 23–26. Every reservoir, filterable by basin, charted as
  usable volume % or upstream level.

Shared controls:

- **Date range** — presets (30D through Max) or explicit from/to dates.
- **Smoothing** — daily, 7-day, or 30-day moving average. The average is computed
  over full history, so the first days of your window aren't clipped.
- **Subsystems** — SIN, SE, S, NE, N; toggling one fans every picked metric across
  the selected subsystems.
- **Series** — up to 8 at once. Each series keeps its color for as long as it's
  selected, so deselecting one never repaints the others. On the plant and
  reservoir tabs a selection takes the whole metric set for that entity or none
  of it, and selected entities sort to the top of the list.
- **Charts** — one panel per unit (MWmed, MWmês, %, R$/MWh). Measures with
  different scales get their own panel rather than a second y-axis. Crosshair and
  tooltip on hover.
- **Table + CSV** — the current selection as a table, and a CSV export of exactly
  what's on screen.

## Hosting it online

The dashboard is a single static file with the data already inside it, so it
needs no server, no database and no API — filtering, date ranges and charts all
run in the browser. That makes hosting nearly trivial: publish one file.

The repo ships a GitHub Actions workflow (`.github/workflows/refresh.yml`) that
rebuilds the data every evening and deploys it to GitHub Pages. Once it's set up
you never touch it again.

### One-time setup

**The scripted way.** With [git](https://git-scm.com/download/win) and the
[GitHub CLI](https://cli.github.com) installed and `gh auth login` done once:

*Windows (PowerShell)* — from the project folder:

```powershell
.\setup_github.ps1
.\setup_github.ps1 -Name ons-dashboard      # if you want a different repo name
```

If PowerShell refuses to run it ("running scripts is disabled on this system"),
that's the default execution policy. Either run it as:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_github.ps1
```

or unblock scripts for your own account once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**If the script ever fails with `NativeCommandError`:** that is Windows
PowerShell 5.1 treating a native tool's *stderr* as a fatal error even when the
tool succeeded — `gh auth status` writes its status to stderr on purpose. The
script sets `$ErrorActionPreference = "Continue"` and wraps every `git`/`gh`
call in try/catch to avoid it, judging success by `$LASTEXITCODE` instead. If
you still hit it, the manual steps below do exactly the same thing.

**Doing it entirely by hand** — the script is only a wrapper around these:

```powershell
git init
git branch -M main
git config core.autocrlf false
git add -A
git commit -m "ONS balances dashboard"
gh repo create ons-balances --public --source=. --remote=origin
git push -u origin main
gh api -X POST repos/:owner/ons-balances/pages -f build_type=workflow
gh workflow run refresh.yml
```

If the `pages` call errors, set it in the browser instead: Settings → Pages →
Build and deployment → Source: GitHub Actions.

*macOS / Linux:*

```bash
./setup_github.sh ons-balances
```

Either script creates the repo, pushes this directory, switches Pages to the
GitHub Actions source, starts the first build, and prints your URLs. Both are
safe to re-run — against an existing repo they just commit and push.

**By hand**, if you'd rather see each step:

1. **Create a public repo** and push these files. `.gitignore` already excludes
   `raw/`, `data/` and the built HTML — none of that belongs in git, and CI keeps
   them in the Actions cache instead. Only nine small files get committed.

   ```bash
   git init && git add . && git commit -m "ONS balances dashboard"
   git branch -M main
   git remote add origin git@github.com:<you>/ons-balances.git
   git push -u origin main
   ```

2. **Turn on Pages**: repo → Settings → Pages → *Build and deployment* →
   Source: **GitHub Actions**. Do not pick "Deploy from a branch"; the workflow
   uploads the site as an artifact instead of committing it.

3. **Run it once by hand**: Actions tab → *Refresh ONS dashboard* → *Run
   workflow*. The first run downloads everything and takes 10–20 minutes. When it
   finishes, your site is at `https://<you>.github.io/ons-balances/`.

After that it runs itself at 20:40 UTC daily, after ONS's second publish.

### What the workflow does

```
verify  →  fetch  →  build  →  health gate  →  deploy
```

The health gate is the part worth knowing about. Between `build` and `deploy` it
runs `ons_pipeline.py health`, which fails the job if the store is stale, has too
few rows, or has lost plants or reservoirs. **A failed gate means no deploy** —
yesterday's working dashboard stays up rather than being replaced by a broken
one, and you get the usual failed-workflow email.

Tune the thresholds in the workflow to match what you actually expect:

```yaml
python ons_pipeline.py health \
  --max-age-days 5 --min-rows 100000 --min-series 15 \
  --min-plants 50 --min-reservoirs 50
```

Raw ONS files are cached under a key that changes monthly. Within a month the
cache hits and isn't rewritten, so only the current month's file is re-downloaded
each day — a normal daily run is a couple of minutes.

### Cost and limits

Everything here is free, with a lot of headroom:

| | Limit | This dashboard |
|---|---|---|
| Actions minutes | Free for public repos on standard runners | ~2 min/day |
| Pages site size | 1 GB | ~1 MB |
| Pages bandwidth | 100 GB/month (soft) | ~0.8 MB per visit, gzipped |
| Pages build timeout | 10 minutes | seconds — the data work happens in the build job |

**A note if you work on Windows.** The Actions runner is Linux, and a shell
script with CRLF line endings fails there in confusing ways. `.gitattributes`
forces LF on every file the runner touches, and `setup_github.ps1` sets
`core.autocrlf false` before the first commit. You don't need to do anything —
but if you ever add a shell step to the workflow, leave those settings alone.

Three caveats that bite people:

- **Scheduled workflows are disabled after 60 days of repository inactivity.** The
  workflow writes a one-line `.state/last_refresh.json` and commits it each run,
  which counts as activity and keeps the cron alive. It's also a handy record of
  when the data last updated. The commit is tagged `[skip ci]` so it doesn't
  trigger another run.
- **GitHub's cron is best-effort.** A daily job can be delayed by 10–30 minutes at
  peak times, occasionally more. Nothing here is time-critical, but don't read the
  schedule as a guarantee.
- **A public Pages site is genuinely public** — no login, indexed by search
  engines. The ONS data is CC-BY so republishing it is permitted, and attribution
  is already in the page footer. Whether an S&P employee publishing a market-data
  dashboard under their own name needs a nod from compliance is your call, not
  mine, but it's worth two minutes of thought before you push.

### If you later want it private

GitHub can serve an access-controlled Pages site, but only on **Enterprise
Cloud** — it is not available on Free or Pro. The practical alternative is
**Cloudflare Pages** with **Cloudflare Access** in front of it: same static file,
same workflow (swap the deploy step for `cloudflare/pages-action`), and Access
gates the site by email address, free for up to 50 users. Say the word and I'll
write that variant.

### Keeping it a single file

Everything the page needs is embedded, which is why it works equally well from
GitHub Pages, from a file:// path, or as an email attachment. If the payload ever
grows past comfortable — say you add hourly granularity — the next step is to
split the plant and reservoir data into separate `.json.gz` files fetched when
those tabs open. That trades portability for a faster first paint. At today's
size (~0.8 MB over the wire, about a second to interactive) it isn't worth it.

## Caveats worth knowing

- **ONS revises.** Recent days get restated after publication. The pipeline
  re-downloads changed files, so a refresh picks revisions up, but a figure you
  quoted last week may not match today.
- **CMO is not PLD.** CMO is ONS's DESSEM marginal cost. PLD is CCEE's settlement
  price and comes from a different source; they track each other but are not the
  same number.
- **The SIN row is derived, not published.** Absolute series are summed across
  subsystems. EAR % is rebuilt as summed stored ÷ summed capacity. ENA % of MLT is
  rebuilt by summing each subsystem's implied MLT. CMO for SIN is an unweighted
  mean of the four subsystem CMOs — a reference level, not a traded price.
- **Fuel classification is string matching** on `nom_tipocombustivel`. Natural gas
  captures "Gás Natural", GNL/LNG, and process gas. If ONS introduces a new fuel
  label it lands in "Thermal — other"; the mapping is `classify_fuel()` in
  `ons_pipeline.py`, near the top of the aggregation section.
- **Deviation % is computed, not published.** It is `100 × (verified −
  programmed) / programmed`, from the smoothed components, and is left blank when
  programmed generation is zero — which is why a plant that was scheduled off but
  ran anyway shows a gap rather than an infinite deviation. The bulletin prints
  −100% in that case.
- **Reservoir coverage.** The open-data hydraulic file carries every reservoir ONS
  tracks; the bulletin's sheets 23–26 print the principal ones. The store is a
  superset, so a name in the bulletin should always be present, but not the
  reverse.
- **MWmed vs MWmês.** The balance and generation series are in MWmed (average MW).
  ENA and EAR are in MWmês, which is why they sit in a separate chart panel.

## Files

```
.github/workflows/refresh.yml   daily rebuild + GitHub Pages deploy
.gitattributes     forces LF endings so the Linux runner can parse the workflow
tools/stamp.py     writes .state/last_refresh.json after each successful build
setup_github.ps1   Windows: creates the repo, pushes, enables Pages, first run
setup_github.sh    same thing for macOS / Linux
ons_pipeline.py    downloader + aggregator + CLI
dashboard.py       HTML/JS dashboard generator (imported by the CLI)
check_bulletin.py  reconciles the store against a DIARIO_*.xlsx workbook
make_mock.py       generates ONS-shaped fake data for offline testing
requirements.txt
raw/               downloaded source files (gitignore this)
data/              daily.parquet, daily.csv, entities.parquet, aggregate cache
ons_dashboard.html the deliverable
```

Source: [ONS Dados Abertos](https://dados.ons.org.br), CC-BY.
