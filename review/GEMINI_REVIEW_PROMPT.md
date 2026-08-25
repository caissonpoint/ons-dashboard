You are doing a code and architecture review of a side project: a Python data
pipeline + single-file HTML/JS dashboard that scrapes Brazilian grid operator (ONS)
open data and visualizes it for a natural gas market analyst. I've attached:

- `PROJECT_CONTEXT.md` — architecture overview, data model, recent change history, and
  a specific list of things I'd like your opinion on
- `dashboard.py` — the dashboard generator (a Python script whose main job is emitting
  a large embedded HTML/JS template)
- `ons_pipeline.py` — the data-fetching/aggregation pipeline and CLI
- `refresh.yml` — the GitHub Actions workflow that runs it daily and deploys to GitHub
  Pages
- `README.md` — existing docs: data sources, setup, and several already-documented
  caveats about unit conventions and derived series

Review this like a senior engineer doing a pull-request review for a solo maintainer,
not a generic "here's what could be improved" essay. Specifically:

1. **Correctness.** Read `ons_pipeline.py`'s aggregation logic — especially
   `classify_fuel`, `add_sin`, `add_derived`, `normalize_balance`, and
   `fuel_split_from_plants` — and `dashboard.py`'s derived-series/KPI computations —
   `fuelMix`, `earRow`, `renderKpis`, `reservoirExtremes`, the basin rollup in
   `renderBasinSummary`. Flag anything that looks wrong, fragile, or likely to
   silently produce a bad number. This is a market-facing tool: a silent miscalculation
   is worse than a visible crash.
2. **The "known open questions" section in `PROJECT_CONTEXT.md`.** Give me your actual
   opinion on each one, not just an acknowledgment that you read it.
3. **Security and CI.** `refresh.yml`'s permissions and secret handling, and the newly
   added external Typekit stylesheet link (the page's first external-origin dependency).
4. **Architecture and maintainability.** Is the single-file-with-embedded-template
   approach (`dashboard.py` generating one big HTML string, no build tooling, no JS
   framework) a reasonable tradeoff at this project's size, or would you push back? Is
   the lack of a committed automated test suite a real risk here, and if so, what's the
   minimum worth adding?
5. **Anything else you notice that I didn't ask about** — I'd rather hear it than not.

If you have web search available, it would be genuinely useful to look up ONS's actual
`nom_combustivel` / `nom_tipocombustivel` value vocabulary (e.g. from the ONS Dados
Abertos portal's metadata for the `geracao_termica_despacho_2_ho` or
`geracao_usina_2_ho` datasets) and sanity-check whether `classify_fuel`'s keyword list
actually covers the real strings ONS publishes — that's the one thing I can't verify
myself from here.

Format your findings as a prioritized list — Critical / High / Medium / Low — each
with the specific file and function (or line, if you can tell), what's wrong or risky,
and a concrete suggested fix. Skip generic praise and skip summarizing what the code
does — I want the problems and open questions.
