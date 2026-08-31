# ONS Balances — Wiki

A gas-first dashboard for Brazil's grid operator (ONS) open data: daily subsystem
balances, plant-level thermal dispatch, installed capacity, and reservoir
storage, aggregated into a single self-contained HTML page. No server, no
database, no API — the data is embedded in the file itself.

This wiki covers what the [README](https://github.com/caissonpoint/ons-dashboard/blob/main/README.md)
doesn't: how to read the live dashboard, how the pieces are deployed and wired
together, and what to take with a grain of salt.

## Pages

- **[Using the Dashboard](Using-the-Dashboard)** — the three tabs, what each
  number means, controls, exports, and the "Refresh data" button. Start here if
  you're reading the live site and want to know what you're looking at.
- **[Architecture and Deployment](Architecture-and-Deployment)** — the
  fetch → build → dashboard pipeline, the CI/CD workflow, and how this repo
  relates to the other gasbrazil.com properties. Start here if you're
  maintaining the code.
- **[Known Limitations and Assumptions](Known-Limitations-and-Assumptions)** —
  every place a number on the dashboard rests on an assumption rather than a
  published ONS figure, plus the standing data-quality caveats. Worth reading
  before quoting a number from this tool to someone else.

## Live sites

| Site | URL | What it is |
|---|---|---|
| ONS Balances (this repo) | [ons.gasbrazil.com](https://ons.gasbrazil.com) | The dashboard this wiki documents |
| POC dashboard | [poc.gasbrazil.com](https://poc.gasbrazil.com) | A separate, related dashboard (`poc-dashboard` repo) |
| Landing page | [gasbrazil.com](https://gasbrazil.com) | Hub linking to both |

Each site also has a firewall-friendly mirror with no custom domain, kept in
sync automatically — see [Architecture and Deployment](Architecture-and-Deployment)
for why and how:

- `ons.gasbrazil.com` → also at [ons-dashboard.github.io](https://ons-dashboard.github.io) and [gasbrazil.github.io/ons](https://gasbrazil.github.io/ons)
- `poc.gasbrazil.com` → also at [caissonpoint.github.io/poc-dashboard](https://caissonpoint.github.io/poc-dashboard) and [gasbrazil.github.io/poc](https://gasbrazil.github.io/poc)
- `gasbrazil.com` → also at [gasbrazil.github.io](https://gasbrazil.github.io)

## Repo at a glance

```
ons_pipeline.py     downloader + aggregator + CLI
dashboard.py        HTML/JS dashboard generator (imported by the CLI)
make_mock.py        generates ONS-shaped fake data for offline testing
check_bulletin.py   reconciles the store against an ONS DIARIO_*.xlsx bulletin
.github/workflows/refresh.yml   daily rebuild + GitHub Pages deploy (+ mirrors)
fonts/Degular.ttf   self-hosted font, embedded into the page at build time
requirements.txt
```

Full setup, CLI flags, and local usage are in the
[README](https://github.com/caissonpoint/ons-dashboard/blob/main/README.md) —
this wiki assumes you've already got it running and focuses on the dashboard
itself and how it's deployed.
