# Known Limitations and Assumptions

Worth reading before quoting a number from this dashboard to someone else.
Grouped by how confident you should be: published ONS figures, figures this
pipeline derives from published data, and figures that rest on an
assumption ONS doesn't publish at all.

## Assumptions ONS does not publish

- **Heat rate for estimated gas consumption.** ONS does not publish per-plant
  heat rate or thermal efficiency anywhere in its open data (checked: the
  `CVU das Usinas Térmicas` dataset publishes R$/MWh, a cost, not a physical
  consumption rate, with no fuel price disclosed alongside it to back one
  out; EPE's public cost-parameter documents give CAPEX/O&M by technology,
  not heat rate). Estimated gas consumption uses **1,800 kcal/kWh** for
  combined-cycle blocks and **2,500 kcal/kWh** for simple-cycle plants, both
  typical industry figures (~46% and ~34% efficiency respectively), against
  **9,400 kcal/m³** for natural gas (Brazil's standard calorific value). None
  of these three numbers are plant-specific or ONS-sourced.
- **Combined-cycle vs. simple-cycle detection is inferred, not published.** A
  CEG dispatched as multiple named phases is treated as combined-cycle; a
  single-entity CEG is treated as simple-cycle. A single-entity CEG that is
  itself an unrecognized combined-cycle block will be misclassified as
  simple-cycle — there's no published ONS field that distinguishes the two
  configurations directly.
- **A plant whose CEG can't be matched** (an older bulletin predates the CEG
  column, or the plant has since been deactivated or renamed) shows no
  capacity, utilization, or gas-consumption figure — not a zero, a blank.
  `ons_pipeline.py build`'s log reports the unmatched count each run.
- **Phase-level dispatch entities show no capacity/utilization/gas figure of
  their own**, by design — only the whole physical plant (the CEG) has a
  real capacity, so giving each phase the full combined-plant capacity would
  overstate its individual share. This means well-known large combined-cycle
  plants show real generation but a blank capacity/utilization/gas column
  at the phase-row level in the entity table; the combined figure is on the
  synthesized "whole plant" row instead.
- **The five pinned "Total" rows on Thermal Plants are gas-fleet-only**, not
  whole-thermal-fleet — their "Verified" figure is `gen_gas` (gas generation
  only), matching the "Est. gas consumption" column's scope (which was
  always gas-only). Earlier builds briefly mixed an all-thermal figure next
  to a gas-only one on the same row; both columns are gas-scoped now.

## Derived, not published

- **The SIN (national) row is derived, not published by ONS.** Absolute
  series are summed across the four subsystems. EAR % is rebuilt as summed
  stored ÷ summed capacity, not an average of the four subsystem
  percentages. ENA % of MLT is rebuilt by summing each subsystem's implied
  long-term-average. CMO for SIN is an unweighted mean of the four subsystem
  CMOs — a reference level, not a traded price.
- **Deviation % is computed, not published**: `100 × (verified − programmed)
  / programmed`, left blank when programmed generation is zero (the
  bulletin prints −100% in that case instead).
- **Fuel classification is string matching** on ONS's raw `nom_combustivel`
  label (`classify_fuel()` in `ons_pipeline.py`). Real-world labels vary —
  bare `"Gás"` with no qualifier is one example that has bitten this
  project before (see below). An ambiguous label containing "gas" that
  isn't an exact match in `GAS_FUELS` prints a one-time build-log warning
  and falls into "Thermal — other" rather than being guessed as gas —
  deliberately: guessing wrong in the gas-inclusive direction would defeat
  the fuel-split-vs-balance tripwire that catches this class of problem.
- **Net interchange sign** is normalized per calendar year against the
  identity `Load = Production − Interchange`, since ONS has changed its
  reporting convention mid-window before; a year with too few observations
  to decide reliably inherits the whole-history default instead of flipping
  on noise.
- **A missing subsystem's data produces a genuine gap (NaN) for that day**,
  not a silently-understated sum — except for series that structurally only
  ever come from one subsystem (e.g. nuclear generation, which only exists
  in the Southeast/Midwest subsystem); the required non-null count for the
  SIN rollup is computed per series, not one fixed number for every series.

## Standing data-quality notes

- **ONS revises.** Recent days get restated after publication — the
  pipeline re-downloads changed files on every run, so a refresh picks
  revisions up, but a figure quoted last week may not match today.
- **CMO is not PLD.** CMO is ONS's DESSEM marginal cost; PLD is CCEE's
  settlement price, from a different source. They track each other but are
  not the same number.
- **MWmed vs. MWmês.** Balance and generation series are in MWmed (average
  MW); ENA and EAR are in MWmês — different units, which is why they render
  in separate chart panels rather than sharing an axis.
- **Reservoir coverage is a superset of the bulletin's.** The open-data
  hydraulic file carries every reservoir ONS tracks; the bulletin prints
  only the principal ones. A name in the bulletin should always be present
  here, but not the reverse.
- **A stat tile's "as of" date can differ from the tile next to it** —
  different ONS publications (the balance file vs. the thermal dispatch
  file) don't necessarily finish publishing for a given day at the same
  time. Every tile shows the true date its own headline number belongs to
  rather than implying same-day alignment.
- **Browser floor**: the embedded payload is inflated client-side via
  `DecompressionStream`, which needs Chrome/Edge 80+, Firefox 113+, or
  Safari 16.4+. Older browsers get an explanatory message instead of a
  blank page.

## Past data-integrity issues (fixed, kept here for institutional memory)

- **52 of 56 monthly thermal-dispatch files were being skipped outright**
  (2022-01 through 2026-03) because ONS only added the fuel-label column
  around 2026-04 and the aggregator required every column, fuel label
  included, to be present. Real generation numbers were in every skipped
  file — only the label was missing. Fixed by making the fuel-label column
  optional; a plant's fuel type (stable over time) now backfills onto older
  rows recovered from files that never carried the label. If a plant's
  history on the Thermal Plants tab looks unexpectedly short, this class of
  issue is worth ruling out first.
- **The bare label `"Gás"` (no "Natural" qualifier) wasn't recognized**,
  which made gas generation vanish from the Subsystems tab entirely for a
  period — not sparse, absent. `classify_fuel()` now has an exact-match
  check for the bare label alongside the longer `GAS_FUELS` phrases.
- **A KPI tile double-counted combined-cycle plants** — summing every
  gas-labeled entity unconditionally included both a CEG's individual
  dispatch phases *and* the synthesized combined-plant rollup for the same
  generation. Fleet-wide KPI sums now explicitly exclude rolled-up phase
  entities.
