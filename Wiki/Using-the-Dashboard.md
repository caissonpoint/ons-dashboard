# Using the Dashboard

The dashboard has three tabs — **Thermal Plants** (the default landing tab),
**Subsystems**, and **Reservoirs** — sharing one set of date-range, smoothing,
and series-picker controls underneath the tab bar. Every tab opens with a
fixed **KPI strip** summarizing that tab's headline numbers (listed per tab
below), then the entity picker / metric picker, then a **stat tile** for
each series you've picked — sitting directly above its matching chart (e.g.
the Verified tiles sit above the MWmed chart, the Est. gas consumption tiles
above the gas-consumption chart) rather than one tile block followed by all
the charts.

## Thermal Plants

Every dispatched thermal plant, one row per plant, filterable by Region and
Fuel and searchable by name. Columns: Name, Region, Fuel, Installed capacity,
Verified, Est. gas consumption (Programmed, Deviation, and Utilization are
also available as metric toggles above the table).

Five rows are pinned at the top regardless of filters or sort — **Total —
SIN (national)** and one per subsystem (Southeast/Midwest, South, Northeast,
North). These are synthetic rows, not real plants: they chart the *gas-fired*
fleet's generation and estimated consumption for that subsystem, so you can
compare an individual plant against its subsystem or the national total
without switching tabs. Select all/Deselect all include them like any other
visible row, but they're left out of the Fuel dropdown's option list and out
of every fleet-wide KPI sum on this tab (so a subsystem total can't get
counted into "Gas verified generation" alongside the individual plants that
make it up) — see [Known Limitations](Known-Limitations-and-Assumptions) for
exactly what "Verified" on these rows is (and isn't) scoped to.

The tab opens with the five Total rows pre-selected and charted. Applying any
Region/Fuel/search filter replaces the current selection with the top 5
matching rows; clearing all filters clears the selection.

KPI strip: Latest available data, Gas-fired plants online (count with
verified generation > 0 today, out of the total gas fleet), Gas verified
generation (MWmed, % of all thermal plants), Top gas plant (name/region/MW),
Gas fleet utilization (%), Est. gas consumption (m³/day, national), Gas
fleet vs. programmed (average over-/under-delivery vs. the day-ahead
schedule).

## Subsystems

The national/subsystem balance — load, generation by source, thermal by
fuel, hydrology, and CMO — grouped into four picker sections: **Balance**
(Load, Hydro/Gas/Wind/Solar generation, Production total, Net interchange,
Thermal fleet utilization, Est. gas consumption), **Thermal by fuel**
(natural gas, coal, oil/diesel, nuclear, biomass — the full breakdown, gas
duplicated here from Balance for anyone who wants the full fuel detail),
**Hydrology** (ENA, EAR, in both absolute and %-of-long-term-average terms),
and **Prices** (CMO). A Subsystems toggle (SIN/SE/S/NE/N) fans whatever
you've picked across the selected subsystems at once.

Opens with Load, Gas generation, Hydro generation, and EAR % (reservoir
storage) for SIN selected — gas and hydro side by side since hydro
availability is the main swing factor that drives gas dispatch, with
reservoir storage alongside as the leading indicator for it.

KPI strip: Latest available data, SIN load, Gas-fired generation (MW, % of
total generation, 7-day trend), Hydro reservoirs (EAR%, 30-day change),
CMO (spot price), Thermal fleet utilization, Est. gas consumption, and the
subsystem with the largest net export or import. Below the tile strip, a
"Generation by fuel" bar breaks down whichever subsystem(s) are toggled in
Subsystems into Hydro/Gas/Coal/Oil/Nuclear/Biomass/Wind/Solar shares.

## Reservoirs

Every reservoir ONS tracks, filterable by Region and Basin, searchable by
name, charted as usable volume % or upstream level. Above the per-reservoir
picker sits a three-tier summary, coarsest to finest:

1. **Hydro reservoirs by region** — SIN + each subsystem, EAR% with a
   capacity-filled bar, stored/capacity in MWmês, 30-day change, and inflow
   (ENA %MLT).
2. **EAR by Reservoir Equivalent (REE)** — the same storage metric at REE
   granularity (finer than subsystem, coarser than basin) — useful because
   two REEs can move in opposite directions underneath one steady regional
   number.
3. **Usable volume by basin** — reservoir count and avg/min/max usable
   volume %, grouped by basin and sorted lowest-first.

KPI strip: Latest available data, SIN reservoirs (EAR%, 30-day change),
National inflow (ENA % of long-term average, plus a plain-language read on
whether storage is likely recovering or declining), Most-stressed region
(lowest EAR% among the four subsystems), Lowest individual reservoir, and a
count of reservoirs currently below 20% usable volume. Region-table capacity
bars and KPI-tile colors share a red/amber/green banding (with a text label,
not color alone) at <30% / 30–60% / >60%.

## Reading a stat tile

Every stat tile shows the value **as of the exact date it belongs to** —
different ONS publications can lag each other by a day or two, so two tiles
side by side aren't guaranteed to be the same calendar day; the "as of"
line makes that visible instead of implying everything is same-day. The
range note reads "+X% over `N`-day range" or "−X% under `N`-day range"
depending on the sign of the change across your selected window.

## Controls

- **Date range** — presets (30D through Max) or explicit From/To dates.
- **Smoothing** — Daily, 7-day, or 30-day moving average, computed over full
  history so the first days of your window aren't clipped.
- **Region / Fuel / Search** (Thermal Plants, Reservoirs) — each numeric
  metric column also has its own filter (click the ▾ next to a column
  header): show non-zero only, zero only, or a min/max range. Column filters
  AND together with Region/Fuel/Search.
- **Sortable tables** — click any column header: first click sorts
  highest→lowest, second click lowest→highest, and it keeps toggling.
  Applies to all five data tables on the dashboard (the entity pickers, the
  Table view, and the two Reservoirs summary tables).
- **Chart tooltip** — hover directly over a line to see just that series'
  value at the cursor's date. Click a blank spot on the chart to pin the
  full multi-series breakdown for that date (click the same spot again to
  unpin); moving off a line with nothing pinned shows nothing, rather than
  auto-expanding to every series.

## Exports

- **Download CSV** — exactly what's currently plotted on the active tab.
- **Export all data (Excel)** — everything, regardless of what's selected or
  smoothed: four sheets (Subsystems, Thermal Plants, Reservoirs, EAR by REE),
  built from the raw daily payload. Virtual Total rows are excluded from the
  Thermal Plants sheet (their numbers are already on the Subsystems sheet in
  full). Building it can take a few seconds for a full history — the button
  reads "Building…" while it compresses the workbook client-side.

## Refresh data

The header's refresh icon triggers a rebuild on demand — it calls a small
Cloudflare Worker that kicks off the `refresh.yml` GitHub Actions workflow
(see [Architecture and Deployment](Architecture-and-Deployment)). A real
rebuild takes a couple of minutes; the button disables itself for 60 seconds
after triggering since there's nothing on this static page to poll for
progress.

## Getting to the other sites

Top-right of the header: **← GasBrazil.com** and **POC Results Dashboard →**
link to the landing page and the companion POC dashboard. These resolve to
the right URL automatically depending on which mirror you're viewing the
page from (custom domain, `caissonpoint.github.io`, or the `gasbrazil.github.io`
hub) — see [Home](Home) for the full list of mirrors.
