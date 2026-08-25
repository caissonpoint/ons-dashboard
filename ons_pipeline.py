#!/usr/bin/env python3
"""
ONS daily-balances pipeline.

Downloads Brazilian grid operator (ONS) open data, aggregates it to a daily
tidy store, and generates a self-contained HTML dashboard.

Datasets pulled (all from https://dados.ons.org.br, CC-BY):
  balanco   Balanco de Energia nos Subsistemas  (hourly -> daily mean, MWmed)
  geracao   Geracao por Usina em Base Horaria   (hourly -> daily mean by fuel)
  ena       ENA Diario por Subsistema           (daily, MWmes and %MLT)
  ear       EAR Diario por Subsistema           (daily, MWmes and %)
  cmo       CMO Semi-Horario                    (30-min -> daily mean, R$/MWh)
  termica   Geracao Termica por Motivo de Despacho (hourly per plant -> daily
            programmed/verified MWmed; bulletin sheet 09, and the source of the
            thermal-by-fuel splits)
  hidraulico Dados Hidraulicos por Reservatorio    (daily per reservoir: upstream
            level and usable volume %; bulletin sheets 23-26)
  geracao   Geracao por Usina em Base Horaria    (optional, large; only needed if
            you want fuel splits over the full plant universe)

Usage:
  python ons_pipeline.py verify                 # check every source URL is reachable
  python ons_pipeline.py fetch                  # download raw files into ./raw
  python ons_pipeline.py build                  # aggregate raw -> ./data/daily.parquet
  python ons_pipeline.py dashboard              # write ons_dashboard.html
  python ons_pipeline.py health                 # gate a deploy: fresh? complete?
  python ons_pipeline.py refresh                # fetch + build + dashboard (normal daily run)

Common flags:
  --years 2021 2026        inclusive year range to cover (default: last 5 years)
  --datasets balanco ena   restrict to a subset
  --raw DIR --out DIR      override directories
  --force                  re-download even if the local copy looks current
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import requests

S3 = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset"

# Subsystem codes used consistently across every ONS dataset.
SUBSYSTEMS = {
    "SE": "Sudeste/Centro-Oeste",
    "S": "Sul",
    "NE": "Nordeste",
    "N": "Norte",
}

# Fuel strings in nom_tipocombustivel/nom_combustivel that count as natural
# gas. Matched as substrings against the deaccented, lowercased field, so
# "gas natural" also catches "Gás Natural (Ciclo Combinado)" etc. classify_fuel
# additionally treats the bare deaccented label "gas" (ONS's own short form --
# some plants/files report just "Gás" with no qualifier) as an exact match,
# since that's unambiguous on its own; anything else that merely contains
# "gas" gets a one-time stderr warning instead of being guessed at, so a
# genuinely new/ambiguous variant surfaces for a deliberate add here rather
# than being silently folded in (see classify_fuel below).
GAS_FUELS = {"gas natural", "gnl", "lng", "gas de processo", "gas industrial",
             "gas de refinaria", "gas natural liquefeito"}


# --------------------------------------------------------------------------
# Source definitions
# --------------------------------------------------------------------------


@dataclass
class Source:
    key: str
    label: str
    s3_dir: str
    # filename builder: (year, month|None) -> stem without extension
    stem: Callable[[int, int | None], str]
    monthly_from: int | None = None  # year at which ONS switched to monthly files
    parquet_from: int = 2021         # earliest year with a .parquet resource
    first_year: int = 2000
    formats: tuple[str, ...] = ("parquet", "csv")


SOURCES: dict[str, Source] = {
    "balanco": Source(
        key="balanco",
        label="Balanco de Energia nos Subsistemas",
        s3_dir="balanco_energia_subsistema_ho",
        stem=lambda y, m: f"BALANCO_ENERGIA_SUBSISTEMA_{y}",
    ),
    "geracao": Source(
        key="geracao",
        label="Geracao por Usina em Base Horaria",
        s3_dir="geracao_usina_2_ho",
        stem=lambda y, m: (
            f"GERACAO_USINA-2_{y}_{m:02d}" if m else f"GERACAO_USINA-2_{y}"
        ),
        monthly_from=2022,
    ),
    "ena": Source(
        key="ena",
        label="ENA Diario por Subsistema",
        s3_dir="ena_subsistema_di",
        stem=lambda y, m: f"ENA_DIARIO_SUBSISTEMA_{y}",
    ),
    "ear": Source(
        key="ear",
        label="EAR Diario por Subsistema",
        s3_dir="ear_subsistema_di",
        stem=lambda y, m: f"EAR_DIARIO_SUBSISTEMA_{y}",
    ),
    "cmo": Source(
        key="cmo",
        label="CMO Semi-Horario",
        s3_dir="cmo_tm",
        stem=lambda y, m: f"CMO_SEMIHORARIO_{y}",
        first_year=2020,
    ),
    # Bulletin sheet 09 "Producao Termica" - programmed vs verified, per plant.
    "termica": Source(
        key="termica",
        label="Geracao Termica por Motivo de Despacho",
        s3_dir="geracao_termica_despacho_2_ho",
        stem=lambda y, m: (
            f"GERACAO_TERMICA_DESPACHO-2_{y}_{m:02d}" if m
            else f"GERACAO_TERMICA_DESPACHO-2_{y}"
        ),
        monthly_from=2022,
        first_year=2013,
    ),
    # Bulletin sheets 23-26 "Sit. Princ. Reservatorios".
    "hidraulico": Source(
        key="hidraulico",
        label="Dados Hidraulicos por Reservatorio - Base diaria",
        s3_dir="dados_hidrologicos_di",
        stem=lambda y, m: f"DADOS_HIDROLOGICOS_RES_{y}",
    ),
}


def periods(src: Source, y0: int, y1: int) -> list[tuple[int, int | None]]:
    """Yield (year, month|None) file periods a source publishes over the range."""
    out: list[tuple[int, int | None]] = []
    today = dt.date.today()
    for y in range(max(y0, src.first_year), y1 + 1):
        if src.monthly_from and y >= src.monthly_from:
            last_month = today.month if y == today.year else 12
            out.extend((y, m) for m in range(1, last_month + 1))
        else:
            out.append((y, None))
    return out


def url_for(src: Source, y: int, m: int | None, fmt: str) -> str:
    return f"{S3}/{src.s3_dir}/{src.stem(y, m)}.{fmt}"


def preferred_formats(src: Source, y: int) -> list[str]:
    """Parquet where ONS publishes it (no delimiter/encoding ambiguity), else CSV."""
    if y >= src.parquet_from and "parquet" in src.formats:
        return ["parquet", "csv"]
    return ["csv"]


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ons-balances-dashboard/1.0"})


def http_head(url: str, timeout: int = 30) -> requests.Response:
    return SESSION.head(url, timeout=timeout, allow_redirects=True)


def download(url: str, dest: Path, force: bool = False, timeout: int = 300) -> bool:
    """Download url -> dest. Returns True if a new copy was written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        try:
            head = http_head(url)
            remote_len = int(head.headers.get("Content-Length", -1))
            if head.status_code == 200 and remote_len == dest.stat().st_size:
                return False
        except requests.RequestException:
            return False  # keep the local copy rather than lose it to a blip

    tmp = dest.with_suffix(dest.suffix + ".part")
    with SESSION.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return True


def fetch_source(src: Source, y0: int, y1: int, raw: Path, force: bool) -> list[Path]:
    """Download every available file for a source; skip periods ONS hasn't posted."""
    got: list[Path] = []
    for y, m in periods(src, y0, y1):
        for fmt in preferred_formats(src, y):
            url = url_for(src, y, m, fmt)
            dest = raw / src.key / Path(url).name
            try:
                fresh = download(url, dest, force=force)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # try next format, or period genuinely absent
                print(f"  ! {url} -> {e}", file=sys.stderr)
                continue
            except requests.RequestException as e:
                print(f"  ! {url} -> {e}", file=sys.stderr)
                continue
            got.append(dest)
            print(f"  {'downloaded' if fresh else 'cached    '} {dest.name}")
            break
        else:
            print(f"  ! no file found for {src.key} {y}"
                  + (f"-{m:02d}" if m else ""), file=sys.stderr)
    return got


# --------------------------------------------------------------------------
# Reading (parquet preferred; CSV with delimiter sniffing as fallback)
# --------------------------------------------------------------------------


_NUM_TOKEN = re.compile(r"\d[\d.,]*\d")


def sniff_csv(path: Path) -> dict:
    """ONS has shipped both ';' and ',' delimited CSVs over the years."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        head = fh.read(64 * 1024)
    try:
        dialect = csv.Sniffer().sniff(head, delimiters=";,\t")
        sep = dialect.delimiter
    except csv.Error:
        sep = ";" if head.count(";") > head.count(",") else ","
    lines = head.splitlines()
    first_line = lines[0] if lines else ""
    # Decimal comma only if the delimiter is not itself a comma. Judge on a
    # block of rows rather than one sample row, and decide by which separator
    # comes LAST inside a number: in 12.345,60 both a dot and a comma sit
    # between digits, so counting them alone ties and picks wrong.
    decimal = "."
    if sep == ";":
        comma_last = dot_last = 0
        for tok in _NUM_TOKEN.findall("\n".join(lines[1:41])):
            seps = [c for c in tok if c in ".,"]
            if not seps:
                continue
            if seps[-1] == ",":
                comma_last += 1
            else:
                dot_last += 1
        if comma_last > dot_last:
            decimal = ","
    # pt-BR files pair a decimal comma with a '.' thousands separator; pandas
    # needs both or '12.345,60' silently stays text
    thousands = "." if decimal == "," else None
    return {"sep": sep, "decimal": decimal, "thousands": thousands,
            "header_line": first_line}


def read_table(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path, columns=columns)
    opts = sniff_csv(path)
    df = pd.read_csv(
        path,
        sep=opts["sep"],
        decimal=opts["decimal"],
        thousands=opts["thousands"],
        encoding="utf-8",
        low_memory=False,
    )
    df.columns = [c.strip().lower() for c in df.columns]
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise KeyError(
                f"{path.name}: expected columns {missing}; found {list(df.columns)}"
            )
        df = df[columns]
    return df


def norm_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


# --------------------------------------------------------------------------
# Type coercion
#
# ONS metric columns arrive as text often enough that it has to be handled
# rather than assumed away: a decimal comma the sniffer missed, a sentinel
# like '-' or 'ND' in one row, a thousands separator, or a parquet resource
# where the publisher typed the column as string. Since pandas 3.0 (Jan 2026)
# such a column lands as `str` dtype and .mean() raises outright, so coerce
# every metric before it reaches a groupby -- and say so loudly when values
# are lost, because that means the source format moved.
# --------------------------------------------------------------------------

_NULLISH = {"", "-", "--", "nd", "n/d", "na", "n/a", "null", "none", "nan", "s/i"}


def to_number(s: pd.Series) -> pd.Series:
    """Parse a possibly-text column into floats, tolerating pt-BR formatting."""
    if pd.api.types.is_numeric_dtype(s):
        return s
    t = s.astype("string").str.strip()
    t = t.mask(t.str.lower().isin(_NULLISH))
    # Literal characters, not \s / \uXXXX: on pandas 3 these columns are Arrow
    # backed and RE2 rejects \u escapes.
    t = t.str.replace("[ \t\r\n\u00a0]", "", regex=True)
    # 1.234,56 -> 1234.56 ; a bare '1234,56' -> '1234.56' ; '1234.56' untouched
    br = t.str.contains(",", na=False)
    t = t.mask(br, t.str.replace(".", "", regex=False)
                    .str.replace(",", ".", regex=False))
    # plain float64, not nullable Float64 -- the store and the dashboard expect
    # NaN semantics, and a mix of the two dtypes survives concat badly
    return pd.to_numeric(t, errors="coerce").astype("float64")


def coerce_numeric(df: pd.DataFrame, cols: Iterable[str], where: str = "") -> pd.DataFrame:
    """Coerce the named columns in place; report anything that had to be parsed."""
    for c in cols:
        if c not in df.columns:
            continue
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            continue
        conv = to_number(s)
        n_text = int(s.notna().sum())
        lost = int(n_text - conv.notna().sum())
        if n_text and lost > 0.01 * n_text:
            bad = s[conv.isna() & s.notna()].astype(str).unique()[:5]
            print(f"  ! {where}{c}: read as text; {lost:,}/{n_text:,} values would not "
                  f"parse as numbers (e.g. {list(bad)}) -- check the source format",
                  file=sys.stderr)
        else:
            print(f"  . {where}{c}: read as text, coerced to numeric")
        df[c] = conv
    return df


def to_date(s: pd.Series, where: str = "") -> pd.Series:
    """Parse a timestamp column to midnight-normalised dates, NaT on failure."""
    d = pd.to_datetime(s, errors="coerce", format="mixed")
    bad = int(d.isna().sum() - pd.isna(s).sum())
    if bad > 0:
        print(f"  ! {where}{bad:,} row(s) had an unparseable timestamp", file=sys.stderr)
    return d.dt.normalize()


# --------------------------------------------------------------------------
# Column aliases
#
# ONS is not consistent about column names across datasets or across years.
# The thermal dispatch file calls the fuel column `nom_combustivel`, while
# Geracao por Usina calls the same thing `nom_tipocombustivel`. A mismatch
# here does not raise anywhere visible -- the file is skipped and the dataset
# quietly contributes nothing -- so resolve names rather than assume them.
# --------------------------------------------------------------------------

COL_ALIASES: dict[str, tuple[str, ...]] = {
    "nom_combustivel": ("nom_tipocombustivel", "nom_tipo_combustivel"),
    "nom_tipocombustivel": ("nom_combustivel", "nom_tipo_combustivel"),
    "val_proggeracao": ("val_geracaoprogramada",),
    "val_verifgeracao": ("val_geracaoverificada",),
    "val_geracao": ("val_geracaoverificada",),
}


def resolve_columns(available: Iterable[str], want: list[str]) -> dict[str, str]:
    """Map each wanted canonical name to the column actually present."""
    have = {str(c).strip().lower(): str(c) for c in available}
    out, missing = {}, []
    for c in want:
        for cand in (c,) + COL_ALIASES.get(c, ()):
            if cand in have:
                out[c] = have[cand]
                break
        else:
            missing.append(c)
    if missing:
        raise KeyError(f"missing {missing}; file has {sorted(have)}")
    return out


# --------------------------------------------------------------------------
# Aggregation to daily tidy frame: date | subsystem | series | value
# --------------------------------------------------------------------------

COLS = ["date", "subsystem", "entity", "series", "value"]

BALANCO_METRICS = {
    "val_carga": "load",
    "val_gerhidraulica": "gen_hydro",
    "val_gertermica": "gen_thermal",
    "val_gereolica": "gen_wind",
    "val_gersolar": "gen_solar",
    "val_intercambio": "net_interchange",
}


def agg_balanco(paths: Iterable[Path]) -> pd.DataFrame:
    cols = ["din_instante", "id_subsistema"] + list(BALANCO_METRICS)
    frames = []
    for p in paths:
        df = norm_columns(read_table(p))
        missing = [c for c in ("din_instante", "id_subsistema") if c not in df.columns]
        if missing:
            print(f"  ! skipping {p.name}: missing {missing}", file=sys.stderr)
            continue
        have = [c for c in cols if c in df.columns]
        df = df[have].copy()
        metrics = [c for c in BALANCO_METRICS if c in df.columns]
        if not metrics:
            print(f"  ! skipping {p.name}: no balance metrics present", file=sys.stderr)
            continue
        df = coerce_numeric(df, metrics, where=f"{p.name}: ")
        df["date"] = to_date(df["din_instante"], where=f"{p.name}: ")
        df = df.dropna(subset=["date"])
        g = df.groupby(["date", "id_subsistema"], observed=True)[metrics].mean()
        frames.append(g.reset_index())
    if not frames:
        return pd.DataFrame(columns=["date", "subsystem", "series", "value"])
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"id_subsistema": "subsystem"})
    out = out.melt(id_vars=["date", "subsystem"], var_name="col", value_name="value")
    out["series"] = out["col"].map(BALANCO_METRICS)
    out = out.dropna(subset=["series"])
    out["entity"] = ""
    return out[["date", "subsystem", "entity", "series", "value"]]


def deaccent(s: str) -> str:
    """ONS mixes accented and unaccented spellings across years and files."""
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s or ""))
        if not unicodedata.combining(c)
    ).strip().lower()


_WARNED_AMBIGUOUS_FUELS: set[str] = set()


def classify_fuel(tipo_usina: str, combustivel: str) -> str | None:
    """Map an ONS plant row to a thermal fuel series; None for non-thermal.

    Unknown fuels deliberately fall into ``thermal_other`` rather than being
    guessed at -- build_store's >3% fuel-split tripwire is what's supposed to
    catch a real gap between the balance thermal total and the plant-level
    splits, and a blind "contains 'gas' -> gas" catch-all defeated that by
    silently absorbing any ambiguous string (a waste-gas or synthesis-gas
    variant, say) before the tripwire ever saw it. Anything that still
    contains "gas" after the explicit checks above gets a one-time stderr
    warning instead, so it surfaces fast and can be added to GAS_FUELS
    deliberately rather than folded in blindly.
    """
    t = deaccent(tipo_usina)
    if not t.startswith("term"):  # TERMICA / TÉRMICA
        return None
    f = deaccent(combustivel)
    if any(k in f for k in GAS_FUELS):
        return "thermal_gas"
    if f == "gas":  # ONS's own bare label on some plants/files -- unambiguous
        return "thermal_gas"
    if "nuclear" in f or "uranio" in f:
        return "thermal_nuclear"
    if any(k in f for k in ("oleo", "diesel", "fossil")):
        return "thermal_oil"
    if ("carvao" in f and "vegetal" not in f) or "coal" in f:
        return "thermal_coal"
    if any(k in f for k in ("biomassa", "bagaco", "licor", "residuo", "biogas",
                            "cavaco", "casca", "capim", "carvao vegetal")):
        return "thermal_biomass"
    if "gas" in f and combustivel not in _WARNED_AMBIGUOUS_FUELS:
        _WARNED_AMBIGUOUS_FUELS.add(combustivel)
        print(f"  ! ambiguous fuel contains \"gas\" but isn't in GAS_FUELS -- "
              f"classified as thermal_other, NOT counted as gas: {combustivel!r} "
              f"(add it to GAS_FUELS in ons_pipeline.py if it should count)",
              file=sys.stderr)
    return "thermal_other"


GERACAO_COLS = ["din_instante", "id_subsistema", "nom_tipousina",
                "nom_tipocombustivel", "val_geracao"]


def _geracao_chunks(path: Path):
    """Yield frames from one generation file without loading it whole."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        mapping = resolve_columns(pf.schema_arrow.names, GERACAO_COLS)
        back = {v.strip().lower(): k for k, v in mapping.items()}
        for batch in pf.iter_batches(batch_size=500_000,
                                     columns=[mapping[c] for c in GERACAO_COLS]):
            yield norm_columns(batch.to_pandas()).rename(columns=back)
    else:
        opts = sniff_csv(path)
        reader = pd.read_csv(path, sep=opts["sep"], decimal=opts["decimal"],
                             thousands=opts["thousands"], encoding="utf-8",
                             chunksize=500_000, low_memory=False)
        for chunk in reader:
            chunk = norm_columns(chunk)
            mapping = resolve_columns(chunk.columns, GERACAO_COLS)
            yield chunk.rename(columns={v: k for k, v in mapping.items()})


def agg_geracao_file(path: Path) -> pd.DataFrame:
    """Hourly plant-level generation -> daily mean MWmed by subsystem and fuel."""
    hourly_parts = []
    for chunk in _geracao_chunks(path):
        chunk = norm_columns(chunk)
        missing = [c for c in GERACAO_COLS if c not in chunk.columns]
        if missing:
            raise KeyError(f"missing {missing}; found {list(chunk.columns)}")
        chunk = chunk[GERACAO_COLS].copy()
        chunk = coerce_numeric(chunk, ["val_geracao"], where=f"{path.name}: ")
        # classify on the small set of distinct (tipo, combustivel) pairs, not row-wise
        pairs = chunk[["nom_tipousina", "nom_tipocombustivel"]].drop_duplicates()
        pairs["series"] = [classify_fuel(a, b) for a, b in
                           zip(pairs["nom_tipousina"], pairs["nom_tipocombustivel"])]
        chunk = chunk.merge(pairs, on=["nom_tipousina", "nom_tipocombustivel"],
                            how="left").dropna(subset=["series"])
        if chunk.empty:
            continue
        # sum plants within each hour first, so the daily figure is a true MWmed mean
        hourly_parts.append(
            chunk.groupby(["din_instante", "id_subsistema", "series"],
                          observed=True)["val_geracao"].sum().reset_index()
        )
    if not hourly_parts:
        return pd.DataFrame(columns=COLS)
    hourly = pd.concat(hourly_parts, ignore_index=True)
    hourly = hourly.groupby(["din_instante", "id_subsistema", "series"],
                            observed=True)["val_geracao"].sum().reset_index()
    hourly["date"] = to_date(hourly["din_instante"], where=f"{path.name}: ")
    hourly = hourly.dropna(subset=["date"])
    daily = hourly.groupby(["date", "id_subsistema", "series"],
                           observed=True)["val_geracao"].mean().reset_index()
    daily = daily.rename(columns={"id_subsistema": "subsystem",
                                  "val_geracao": "value"})
    daily["entity"] = ""
    return daily[COLS]


def agg_geracao(paths: Iterable[Path], cache: Path | None = None) -> pd.DataFrame:
    frames = []
    for p in paths:
        try:
            df = cached_agg(p, agg_geracao_file, cache)
        except Exception as e:
            print(f"  ! skipping {p.name}: {e}", file=sys.stderr)
            continue
        if df.empty:
            print(f"  ! {p.name}: no thermal rows matched — check nom_tipousina values",
                  file=sys.stderr)
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)[COLS]


# --------------------------------------------------------------------------
# Bulletin sheet 09: thermal generation per plant, programmed vs verified
# --------------------------------------------------------------------------

# The thermal dispatch file names the fuel column `nom_combustivel`. The
# longer `nom_tipocombustivel` belongs to Geracao por Usina; COL_ALIASES
# accepts either so both spellings resolve. It's treated as optional (see
# `_chunks`/`agg_termica_file` below) -- ONS files before ~2026-04 don't
# carry this column at all, but do carry the actual generation values, so
# it must not gate whether a file's real numbers get used.
TERMICA_REQUIRED = ["din_instante", "id_subsistema", "nom_usina",
                    "val_proggeracao", "val_verifgeracao"]
TERMICA_OPTIONAL = ["nom_combustivel"]

TERMICA_METRICS = {"val_proggeracao": "plant_prog",
                   "val_verifgeracao": "plant_verif"}


def _chunks(path: Path, want: list[str], optional: list[str] = ()):
    """Yield frames from one file, column-subset, without loading it whole.

    Columns are resolved through COL_ALIASES and renamed to the canonical
    names, so downstream code sees one stable schema. `want` columns must
    all be present in the file's schema or the whole file is skipped
    (`resolve_columns` raises `KeyError`, uncaught here -- callers like
    `agg_termica`/`agg_hidraulico` catch it per-file). `optional` columns
    are included when the file's schema happens to carry them and silently
    left out of the yielded frame otherwise -- callers check for their
    presence via the yielded frame's own columns rather than assuming they
    exist, so a missing optional column degrades that one attribute instead
    of discarding the file's required columns along with it.
    """
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        names = pf.schema_arrow.names
        mapping = resolve_columns(names, want)
        have_opt = []
        for c in optional:
            try:
                mapping.update(resolve_columns(names, [c]))
                have_opt.append(c)
            except KeyError:
                pass
        cols = want + have_opt
        back = {v.strip().lower(): k for k, v in mapping.items()}
        for batch in pf.iter_batches(batch_size=500_000,
                                     columns=[mapping[c] for c in cols]):
            chunk = norm_columns(batch.to_pandas()).rename(columns=back)
            yield chunk[cols]
    else:
        opts = sniff_csv(path)
        for chunk in pd.read_csv(path, sep=opts["sep"], decimal=opts["decimal"],
                                 thousands=opts["thousands"], encoding="utf-8",
                                 chunksize=500_000, low_memory=False):
            chunk = norm_columns(chunk)
            mapping = resolve_columns(chunk.columns, want)
            have_opt = []
            for c in optional:
                try:
                    mapping.update(resolve_columns(chunk.columns, [c]))
                    have_opt.append(c)
                except KeyError:
                    pass
            cols = want + have_opt
            back = {v: k for k, v in mapping.items()}
            yield chunk[[mapping[c] for c in cols]].rename(columns=back)


def agg_termica_file(path: Path) -> pd.DataFrame:
    """Hourly per-plant programmed/verified MWmed -> daily mean per plant.

    The fuel label (`nom_combustivel`) is optional per file, not required --
    see the `_chunks` docstring for why. A file without it still contributes
    its real `plant_prog`/`plant_verif` numbers; it just doesn't contribute
    to the entity->fuel map for those rows. That's fine: a plant's identity
    (name + subsystem) is stable over time, so the fuel map built from
    whichever files DO carry the label (recent ones, so far) still applies
    to that plant's full history once `fuel_split_from_plants` merges the
    two by entity/subsystem -- it isn't merged by file or by date.
    """
    parts, fuels = [], []
    for chunk in _chunks(path, TERMICA_REQUIRED, optional=TERMICA_OPTIONAL):
        chunk = chunk.copy()
        chunk = coerce_numeric(chunk, list(TERMICA_METRICS), where=f"{path.name}: ")
        chunk["date"] = to_date(chunk["din_instante"], where=f"{path.name}: ")
        chunk = chunk.dropna(subset=["date"])
        if chunk.empty:
            continue
        chunk["nom_usina"] = chunk["nom_usina"].astype(str).str.strip()
        if "nom_combustivel" in chunk.columns:
            fuels.append(chunk[["nom_usina", "id_subsistema",
                                "nom_combustivel"]].drop_duplicates())
        # a plant appears once per hour, so the daily mean is a straight mean
        parts.append(
            chunk.groupby(["date", "id_subsistema", "nom_usina"], observed=True)[
                list(TERMICA_METRICS)].mean().reset_index()
        )
    if not parts:
        return pd.DataFrame(columns=COLS), pd.DataFrame()
    daily = pd.concat(parts, ignore_index=True).groupby(
        ["date", "id_subsistema", "nom_usina"], observed=True
    )[list(TERMICA_METRICS)].mean().reset_index()

    out = daily.melt(id_vars=["date", "id_subsistema", "nom_usina"],
                     value_vars=list(TERMICA_METRICS),
                     var_name="col", value_name="value")
    out["series"] = out["col"].map(TERMICA_METRICS)
    out = out.rename(columns={"id_subsistema": "subsystem",
                              "nom_usina": "entity"})
    out = out.dropna(subset=["value"])

    # plant -> fuel map travels alongside the values, as a tiny attribute
    # table -- empty when this particular file never had nom_combustivel.
    fmap = pd.DataFrame(columns=["entity", "subsystem", "group"])
    if fuels:
        fmap = pd.concat(fuels, ignore_index=True).drop_duplicates(
            subset=["id_subsistema", "nom_usina"])
        fmap = fmap.rename(columns={"nom_usina": "entity", "id_subsistema": "subsystem",
                                    "nom_combustivel": "group"})
    return out[COLS], fmap


def agg_termica(paths: Iterable[Path], cache: Path | None = None) -> pd.DataFrame:
    frames, ents = [], []
    for p in paths:
        try:
            df, side = cached_agg(p, agg_termica_file, cache, has_side=True)
        except Exception as e:
            print(f"  ! skipping {p.name}: {e}", file=sys.stderr)
            continue
        if df.empty:
            continue
        frames.append(df)
        if side is not None and not side.empty:
            ents.append(side)
    if not frames:
        return pd.DataFrame(columns=COLS), pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)[COLS]
    e = pd.DataFrame()
    if ents:
        e = pd.concat(ents, ignore_index=True).drop_duplicates(
            subset=["subsystem", "entity"], keep="last")
        e["kind"] = "plant"
    return out, e


# --------------------------------------------------------------------------
# Bulletin sheets 23-26: reservoir level and usable volume
# --------------------------------------------------------------------------

HIDRO_COLS = ["din_instante", "id_subsistema", "nom_bacia", "nom_reservatorio",
              "val_nivelmontante", "val_volumeutilcon"]

HIDRO_METRICS = {"val_nivelmontante": "res_level_m",
                 "val_volumeutilcon": "res_volutil_pct"}


def agg_hidraulico_file(path: Path) -> pd.DataFrame:
    """Already daily, one row per reservoir per day."""
    parts, ents = [], []
    for chunk in _chunks(path, HIDRO_COLS):
        chunk = chunk.copy()
        chunk = coerce_numeric(chunk, list(HIDRO_METRICS), where=f"{path.name}: ")
        chunk["date"] = to_date(chunk["din_instante"], where=f"{path.name}: ")
        chunk = chunk.dropna(subset=["date"])
        if chunk.empty:
            continue
        chunk["nom_reservatorio"] = chunk["nom_reservatorio"].astype(str).str.strip()
        ents.append(chunk[["nom_reservatorio", "id_subsistema",
                           "nom_bacia"]].drop_duplicates())
        parts.append(chunk[["date", "id_subsistema", "nom_reservatorio"]
                           + list(HIDRO_METRICS)])
    if not parts:
        return pd.DataFrame(columns=COLS), pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    # ONS occasionally repeats a reservoir-day after a revision; keep the mean
    df = df.groupby(["date", "id_subsistema", "nom_reservatorio"],
                    observed=True)[list(HIDRO_METRICS)].mean().reset_index()
    out = df.melt(id_vars=["date", "id_subsistema", "nom_reservatorio"],
                  value_vars=list(HIDRO_METRICS),
                  var_name="col", value_name="value")
    out["series"] = out["col"].map(HIDRO_METRICS)
    out = out.rename(columns={"id_subsistema": "subsystem",
                              "nom_reservatorio": "entity"})
    out = out.dropna(subset=["value"])
    emap = pd.concat(ents, ignore_index=True).drop_duplicates(
        subset=["id_subsistema", "nom_reservatorio"])
    emap = emap.rename(columns={"nom_reservatorio": "entity",
                                "id_subsistema": "subsystem", "nom_bacia": "group"})
    return out[COLS], emap


def agg_hidraulico(paths: Iterable[Path], cache: Path | None = None) -> pd.DataFrame:
    frames, ents = [], []
    for p in paths:
        try:
            df, side = cached_agg(p, agg_hidraulico_file, cache, has_side=True)
        except Exception as e:
            print(f"  ! skipping {p.name}: {e}", file=sys.stderr)
            continue
        if df.empty:
            continue
        frames.append(df)
        if side is not None and not side.empty:
            ents.append(side)
    if not frames:
        return pd.DataFrame(columns=COLS), pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)[COLS]
    e = pd.DataFrame()
    if ents:
        e = pd.concat(ents, ignore_index=True).drop_duplicates(
            subset=["subsystem", "entity"], keep="last")
        e["kind"] = "reservoir"
    return out, e


def cached_agg(path: Path, fn, cache: Path | None, has_side: bool = False):
    """Aggregate one raw file, reusing a cached result while the source is unchanged.

    `fn` returns either a frame, or (frame, side_table) when has_side is set.
    """
    st = path.stat()
    # AGG_VERSION invalidates every cached result when the aggregation logic
    # changes, so a fix does not sit behind results produced by the old code.
    # Bumped to v3 for the fix making nom_combustivel optional in
    # agg_termica_file -- old cached termica results were built before that
    # file could even successfully aggregate (it raised and was skipped), so
    # this mainly forces every previously-skipped termica file to actually
    # run once under the new code, plus a one-time re-aggregate of anything
    # else that shares this cache helper.
    tag = f"{path.stem}__v3_{st.st_size}_{int(st.st_mtime)}"
    hit = (cache / f"{tag}.parquet") if cache else None
    side_path = (cache / f"{tag}.side.parquet") if cache else None

    if hit is not None and hit.exists():
        print(f"  cached     {path.name}")
        out = pd.read_parquet(hit)
        if not has_side:
            return out
        side = pd.read_parquet(side_path) if side_path.exists() else pd.DataFrame()
        return out, side

    res = fn(path)
    out, side = res if has_side else (res, None)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        for stale in cache.glob(f"{path.stem}__*.parquet"):
            stale.unlink()
        out.to_parquet(hit, index=False)
        if side is not None and not side.empty:
            side.to_parquet(side_path, index=False)
    print(f"  aggregated {path.name} ({len(out):,} daily rows)")
    return (out, side) if has_side else out


def agg_simple(paths: Iterable[Path], date_col: str,
               metrics: dict[str, str]) -> pd.DataFrame:
    """Already-daily datasets (ENA, EAR): just rename and melt."""
    frames = []
    for p in paths:
        df = norm_columns(read_table(p))
        if date_col not in df.columns:
            print(f"  ! skipping {p.name}: no {date_col}", file=sys.stderr)
            continue
        keep = [c for c in metrics if c in df.columns]
        if not keep:
            continue
        df = df[["id_subsistema", date_col] + keep].copy()
        df = coerce_numeric(df, keep, where=f"{p.name}: ")
        df["date"] = to_date(df[date_col], where=f"{p.name}: ")
        df = df.dropna(subset=["date"])
        out = df.melt(
            id_vars=["date", "id_subsistema"], value_vars=keep,
            var_name="col", value_name="value",
        )
        out["series"] = out["col"].map(metrics)
        out["entity"] = ""
        frames.append(out.rename(columns={"id_subsistema": "subsystem"}))
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)[COLS]


def agg_cmo(paths: Iterable[Path]) -> pd.DataFrame:
    frames = []
    for p in paths:
        df = norm_columns(read_table(p))
        if "val_cmo" not in df.columns or "din_instante" not in df.columns:
            print(f"  ! skipping {p.name}: no val_cmo/din_instante", file=sys.stderr)
            continue
        df = coerce_numeric(df, ["val_cmo"], where=f"{p.name}: ")
        df["date"] = to_date(df["din_instante"], where=f"{p.name}: ")
        df = df.dropna(subset=["date"])
        g = df.groupby(["date", "id_subsistema"], observed=True)["val_cmo"].mean()
        out = g.reset_index().rename(
            columns={"id_subsistema": "subsystem", "val_cmo": "value"}
        )
        out["series"] = "cmo"
        out["entity"] = ""
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)[COLS]


ENA_METRICS = {
    "ena_bruta_regiao_mwmed": "ena_gross_mwmes",
    "ena_bruta_regiao_percentualmlt": "ena_pct_mlt",
    "ena_armazenavel_regiao_mwmed": "ena_storable_mwmes",
    "ena_armazenavel_regiao_percentualmlt": "ena_storable_pct_mlt",
}
EAR_METRICS = {
    "ear_verif_subsistema_mwmes": "ear_mwmes",
    "ear_verif_subsistema_percentual": "ear_pct",
    "ear_max_subsistema": "ear_max_mwmes",
}


def fuel_split_from_plants(df: pd.DataFrame, ent: pd.DataFrame) -> pd.DataFrame:
    """Roll per-plant verified generation up into thermal_<fuel> by subsystem.

    This is the same arithmetic the bulletin does, off the same source, so the
    fuel splits agree with sheet 09 rather than merely resembling it.
    """
    plants = df[df["series"] == "plant_verif"]
    if plants.empty or ent.empty:
        return pd.DataFrame(columns=COLS)
    fuels = ent[ent["kind"] == "plant"][["entity", "subsystem", "group"]].copy()
    fuels["series"] = [classify_fuel("termica", g) for g in fuels["group"]]
    m = plants.merge(fuels[["entity", "subsystem", "series"]],
                     on=["entity", "subsystem"], how="left", suffixes=("_old", ""))
    m = m.dropna(subset=["series"])
    out = m.groupby(["date", "subsystem", "series"],
                    observed=True, as_index=False)["value"].sum()
    out["entity"] = ""
    return out[COLS]


def normalize_balance(df: pd.DataFrame) -> pd.DataFrame:
    """Add Producao total and put net interchange on the bulletin's sign convention.

    Sheets 03-07 of the Boletim Diario satisfy  Carga = Producao total - Intercambio,
    i.e. a positive interchange is a net EXPORT out of the subsystem. ONS's open-data
    val_intercambio has carried both signs over the years -- and has been known to flip
    convention mid-window, not just once at the start of the dataset -- so rather than
    pick one global orientation, decide it per calendar year: whichever orientation
    best reproduces the identity *within that year* is the one applied to it. A year
    with too few observations to decide reliably on its own inherits the whole-history
    default instead of flipping on noise.
    """
    gen = ["gen_hydro", "gen_thermal", "gen_wind", "gen_solar"]
    wide = df[df["entity"] == ""].pivot_table(
        index=["date", "subsystem"], columns="series", values="value", aggfunc="mean")
    have = [c for c in gen if c in wide.columns]
    if len(have) < len(gen) or "load" not in wide.columns:
        return df

    total = wide[have].sum(axis=1, min_count=len(have))
    implied_export = total - wide["load"]          # what the identity requires

    MIN_OBS = 20  # ~5 days x 4 subsystems; below this a year's own residual is noise
    flip_years: set[int] = set()
    if "net_interchange" in wide.columns:
        both = pd.concat([implied_export, wide["net_interchange"]],
                         axis=1, keys=["implied", "reported"]).dropna()
        if len(both):
            def resid(sub, sign):
                return (sub["implied"] + sign * sub["reported"]).abs().median()

            as_is_all, flipped_all = resid(both, -1), resid(both, 1)
            default_flip = flipped_all < as_is_all
            scale_all = both["implied"].abs().median() or 1
            r_all = min(as_is_all, flipped_all)
            print(f"  interchange (whole-history default): "
                  f"{'flip' if default_flip else 'keep'}, residual {r_all:.1f} MWmed "
                  f"({100 * r_all / scale_all:.2f}% of median flow)")

            years = both.index.get_level_values("date").year
            for yr in sorted(years.unique()):
                grp = both[years == yr]
                scale = grp["implied"].abs().median() or 1
                if len(grp) < MIN_OBS:
                    if default_flip:
                        flip_years.add(yr)
                    print(f"  interchange {yr}: only {len(grp)} obs, using "
                          f"whole-history default ({'flip' if default_flip else 'keep'})")
                    continue
                as_is, flipped = resid(grp, -1), resid(grp, 1)
                yr_flip = flipped < as_is
                if yr_flip:
                    flip_years.add(yr)
                r = min(as_is, flipped)
                print(f"  interchange {yr}: {'flip' if yr_flip else 'keep'} "
                      f"net-export sign, residual {r:.1f} MWmed "
                      f"({100 * r / scale:.2f}% of median flow)")

    add = []
    tot = total.reset_index().rename(columns={0: "value"})
    tot.columns = ["date", "subsystem", "value"]
    tot["series"], tot["entity"] = "production_total", ""
    add.append(tot.dropna(subset=["value"]))

    if flip_years:
        m = (df["series"] == "net_interchange") & (df["entity"] == "")
        yr = pd.to_datetime(df["date"]).dt.year
        flip_mask = m & yr.isin(flip_years)
        df.loc[flip_mask, "value"] = -df.loc[flip_mask, "value"]

    return pd.concat([df] + add, ignore_index=True)[COLS]


def build_store(raw: Path, out: Path, keys: list[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    entities: list[pd.DataFrame] = []
    cache = out / "_cache"
    for key in keys:
        d = raw / key
        if not d.exists():
            print(f"  ! no raw files for {key}", file=sys.stderr)
            continue
        files = sorted(p for p in d.iterdir() if p.suffix in (".parquet", ".csv"))
        print(f"[{key}] {len(files)} file(s)")
        if key == "balanco":
            part = agg_balanco(files)
        elif key == "geracao":
            part = agg_geracao(files, cache / "geracao")
        elif key == "ena":
            part = agg_simple(files, "ena_data", ENA_METRICS)
        elif key == "ear":
            part = agg_simple(files, "ear_data", EAR_METRICS)
        elif key == "cmo":
            part = agg_cmo(files)
        elif key == "termica":
            part, ent = agg_termica(files, cache / "termica")
            if not ent.empty:
                entities.append(ent)
        elif key == "hidraulico":
            part, ent = agg_hidraulico(files, cache / "hidraulico")
            if not ent.empty:
                entities.append(ent)
        else:
            continue
        parts.append(part)

    parts = [p for p in parts if not p.empty]
    if not parts:
        raise SystemExit("No data aggregated. Run `fetch` first, or check `verify`.")

    df = pd.concat(parts, ignore_index=True)
    df["subsystem"] = df["subsystem"].astype(str).str.strip().str.upper()
    df = df[df["subsystem"].isin(SUBSYSTEMS)]
    df["entity"] = df["entity"].fillna("").astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    df = (
        df.groupby(["date", "subsystem", "entity", "series"],
                   observed=True, as_index=False)["value"].mean()
    )
    ent_df = pd.DataFrame(columns=["kind", "entity", "subsystem", "group"])
    if entities:
        ent_df = pd.concat(entities, ignore_index=True).drop_duplicates(
            subset=["kind", "subsystem", "entity"], keep="last")
        ent_df["subsystem"] = ent_df["subsystem"].astype(str).str.strip().str.upper()
        ent_df["group"] = ent_df["group"].fillna("").astype(str).str.strip()
        ent_df = ent_df[["kind", "entity", "subsystem", "group"]]

    # Prefer the bulletin's own thermal source for the fuel splits. Geracao por
    # Usina still works when `termica` is not in the run, but it is a much larger
    # download and its plant universe is not the one sheet 09 reports on.
    fuel = fuel_split_from_plants(df, ent_df)
    if not fuel.empty:
        keep = ~df["series"].astype(str).str.startswith("thermal_")
        df = pd.concat([df[keep], fuel], ignore_index=True)
        print(f"  fuel splits derived from per-plant verified generation "
              f"({fuel['series'].nunique()} fuels)")

    df = normalize_balance(df)
    df = df.sort_values(["series", "subsystem", "entity", "date"])

    out.mkdir(parents=True, exist_ok=True)
    dest = out / "daily.parquet"
    df.to_parquet(dest, index=False)
    df.to_csv(out / "daily.csv", index=False)

    ent = ent_df
    ent.to_parquet(out / "entities.parquet", index=False)

    sub = df[df["entity"] == ""]

    # Consistency check worth seeing on every run: the per-fuel splits come from
    # the thermal dispatch file, gen_thermal comes from the balance file. Two
    # different ONS publications - they should agree closely.
    fuel = (sub[sub["series"].str.startswith("thermal_")]
            .groupby(["date", "subsystem"])["value"].sum())
    bal = sub[sub["series"] == "gen_thermal"].set_index(["date", "subsystem"])["value"]
    j = pd.concat([fuel.rename("f"), bal.rename("b")], axis=1).dropna()
    if len(j):
        gap = 100 * (j["f"] - j["b"]).abs() / j["b"].abs().clip(lower=1e-6)
        flag = "" if gap.median() < 3 else "   <-- investigate"
        print(f"  fuel splits vs balance thermal: median {gap.median():.2f}%, "
              f"p95 {gap.quantile(.95):.2f}% over {len(j):,} subsystem-days{flag}")

    print(f"\nWrote {len(df):,} daily rows -> {dest}")
    print(f"  range      {df['date'].min().date()} .. {df['date'].max().date()}")
    print(f"  subsystem  {sorted(sub['series'].unique())}")
    for kind in ent["kind"].unique():
        n = (ent["kind"] == kind).sum()
        print(f"  {kind + 's':<11}{n} entities")
    return df


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_verify(args) -> int:
    ok = True
    for key in args.datasets:
        src = SOURCES[key]
        print(f"\n[{key}] {src.label}")
        checks = periods(src, args.years[0], args.years[1])
        # Probe the first and last period only - enough to catch a URL change.
        for y, m in [checks[0], checks[-1]]:
            found = False
            for fmt in preferred_formats(src, y):
                url = url_for(src, y, m, fmt)
                try:
                    r = http_head(url)
                except requests.RequestException as e:
                    msg = str(e).split("(Caused by")[-1].strip(") ")
                    print(f"  FAIL {Path(url).name}\n       {msg[:160]}")
                    continue
                if r.status_code == 200:
                    size = int(r.headers.get("Content-Length", 0))
                    print(f"  OK   {Path(url).name}  {size/1e6:.1f} MB")
                    found = True
                    break
                print(f"  {r.status_code}  {url}")
            if not found:
                ok = False
    print("\nAll sources reachable." if ok else "\nSome sources failed - see above.")
    return 0 if ok else 1


def cmd_fetch(args) -> int:
    raw = Path(args.raw)
    for key in args.datasets:
        src = SOURCES[key]
        print(f"\n[{key}] {src.label}")
        fetch_source(src, args.years[0], args.years[1], raw, args.force)
    return 0


def cmd_build(args) -> int:
    build_store(Path(args.raw), Path(args.out), args.datasets)
    return 0


def cmd_dashboard(args) -> int:
    from dashboard import write_dashboard

    store = Path(args.out) / "daily.parquet"
    if not store.exists():
        raise SystemExit(f"{store} not found - run `build` first.")
    df = pd.read_parquet(store)
    ent_path = Path(args.out) / "entities.parquet"
    ent = pd.read_parquet(ent_path) if ent_path.exists() else None
    dest = Path(args.html)
    write_dashboard(df, dest, ent)
    print(f"Wrote {dest}  ({dest.stat().st_size/1e6:.1f} MB)")
    return 0


def cmd_health(args) -> int:
    """Gate a deploy: refuse to publish a store that is stale or has lost data.

    CI runs this between `build` and the Pages deploy, so a bad ONS fetch leaves
    yesterday's good dashboard up instead of replacing it with a broken one.
    """
    store = Path(args.out) / "daily.parquet"
    if not store.exists():
        print(f"FAIL  {store} does not exist")
        return 1
    df = pd.read_parquet(store)
    df["date"] = pd.to_datetime(df["date"])
    latest = df["date"].max().date()
    age = (dt.date.today() - latest).days
    sub = df[df["entity"] == ""]

    checks = [
        ("rows", len(df), len(df) >= args.min_rows,
         f">= {args.min_rows:,}"),
        ("staleness", f"{age}d (latest {latest})", age <= args.max_age_days,
         f"<= {args.max_age_days}d"),
        ("subsystem series", sub["series"].nunique(),
         sub["series"].nunique() >= args.min_series, f">= {args.min_series}"),
        ("subsystems", sub["subsystem"].nunique(),
         sub["subsystem"].nunique() >= 4, ">= 4"),
    ]
    ent_path = Path(args.out) / "entities.parquet"
    if ent_path.exists():
        ent = pd.read_parquet(ent_path)
        for kind, floor in (("plant", args.min_plants),
                            ("reservoir", args.min_reservoirs)):
            n = int((ent["kind"] == kind).sum())
            checks.append((kind + "s", n, n >= floor, f">= {floor}"))

    ok = True
    for name, got, passed, want in checks:
        print(f"  {'OK  ' if passed else 'FAIL'} {name:<18} {str(got):<22} want {want}")
        ok &= passed
    print("\nHealthy." if ok else "\nUnhealthy - not safe to deploy.")
    return 0 if ok else 1


def cmd_refresh(args) -> int:
    cmd_fetch(args)
    cmd_build(args)
    if cmd_health(args) != 0:
        return 1
    return cmd_dashboard(args)


def main(argv=None) -> int:
    this_year = dt.date.today().year
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command",
                   choices=["verify", "fetch", "build", "dashboard", "health",
                            "refresh"])
    p.add_argument("--years", nargs=2, type=int,
                   default=[this_year - 4, this_year], metavar=("FROM", "TO"))
    default_sets = [k for k in SOURCES if k != "geracao"]
    p.add_argument("--datasets", nargs="+", default=default_sets,
                   choices=list(SOURCES),
                   help="default: everything except `geracao`, whose fuel splits "
                        "now come from `termica` (same numbers, far smaller download)")
    p.add_argument("--raw", default="raw")
    p.add_argument("--out", default="data")
    p.add_argument("--html", default="ons_dashboard.html")
    p.add_argument("--force", action="store_true")
    p.add_argument("--min-rows", type=int, default=1000,
                   help="health: minimum rows in the store")
    p.add_argument("--max-age-days", type=int, default=5,
                   help="health: how stale the newest date may be")
    p.add_argument("--min-series", type=int, default=10,
                   help="health: minimum distinct subsystem-level series")
    p.add_argument("--min-plants", type=int, default=0)
    p.add_argument("--min-reservoirs", type=int, default=0)
    args = p.parse_args(argv)

    return {
        "verify": cmd_verify,
        "fetch": cmd_fetch,
        "build": cmd_build,
        "dashboard": cmd_dashboard,
        "health": cmd_health,
        "refresh": cmd_refresh,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
