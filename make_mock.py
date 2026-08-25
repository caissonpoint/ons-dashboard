"""Generate ONS-shaped mock raw files so the pipeline can be tested offline."""
import math, random
from pathlib import Path
import numpy as np, pandas as pd

random.seed(7); np.random.seed(7)
RAW = Path("raw")
SUBS = {"SE": "SUDESTE", "S": "SUL", "NE": "NORDESTE", "N": "NORTE"}
YEARS = [2024, 2025, 2026]
SCALE = {"SE": 1.0, "S": 0.28, "NE": 0.22, "N": 0.14}


def hours(y):
    end = f"{y}-12-31 23:00" if y < 2026 else "2026-08-20 23:00"
    return pd.date_range(f"{y}-01-01", end, freq="h")


def season(idx):
    doy = idx.dayofyear.values
    return np.sin(2 * math.pi * (doy - 20) / 365.0)


for y in YEARS:
    idx = hours(y); s = season(idx)
    hod = idx.hour.values
    daily = 1 + 0.12 * np.sin(2 * math.pi * (hod - 15) / 24.0)
    rows = []
    for sub, k in SCALE.items():
        base = 40000 * k
        load = base * daily * (1 + 0.06 * s) * (1 + np.random.normal(0, .02, len(idx)))
        hyd = load * (0.60 + 0.10 * s) * (1 + np.random.normal(0, .05, len(idx)))
        ter = load * (0.16 - 0.08 * s).clip(0.03) * (1 + np.random.normal(0, .18, len(idx)))
        eol = load * (0.14 - 0.05 * s).clip(0.01) * (1 + np.random.normal(0, .25, len(idx)))
        sol = load * np.where((hod > 6) & (hod < 19), 0.10, 0.0)
        rows.append(pd.DataFrame({
            "din_instante": idx, "id_subsistema": sub, "nom_subsistema": SUBS[sub],
            "val_gerhidraulica": hyd, "val_gertermica": ter, "val_gereolica": eol,
            "val_gersolar": sol, "val_carga": load,
            "val_intercambio": hyd + ter + eol + sol - load,
        }))
    d = RAW / "balanco"; d.mkdir(parents=True, exist_ok=True)
    pd.concat(rows).to_parquet(d / f"BALANCO_ENERGIA_SUBSISTEMA_{y}.parquet", index=False)

    # geracao: hourly by plant, subsampled to a handful of plants per fuel
    FUELS = [("Gás Natural", .45), ("Carvão", .10), ("Óleo Diesel", .08),
             ("Nuclear", .07), ("Biomassa", .20), ("Óleo Combustível", .10)]
    grows = []
    for sub, k in SCALE.items():
        ter = 40000 * k * daily * (0.16 - 0.08 * s).clip(0.03)
        for fuel, share in FUELS:
            for p in range(2):
                grows.append(pd.DataFrame({
                    "din_instante": idx, "id_subsistema": sub, "nom_subsistema": SUBS[sub],
                    "id_estado": "SP", "nom_estado": "São Paulo",
                    "cod_modalidadeoperacao": "Tipo I", "nom_tipousina": "TÉRMICA",
                    "nom_tipocombustivel": fuel, "nom_usina": f"UTE {fuel[:4]}{p} {sub}",
                    "id_ons": f"{sub}{p}", "ceg": "X",
                    "val_geracao": ter * share / 2 *
                        (1 + np.random.normal(0, .1, len(idx))),
                }))
        # a couple of hydro rows that must be filtered out
        grows.append(pd.DataFrame({
            "din_instante": idx, "id_subsistema": sub, "nom_subsistema": SUBS[sub],
            "id_estado": "SP", "nom_estado": "São Paulo",
            "cod_modalidadeoperacao": "Tipo I", "nom_tipousina": "HIDRÁULICA",
            "nom_tipocombustivel": "Hídrica", "nom_usina": f"UHE {sub}",
            "id_ons": f"H{sub}", "ceg": "X",
            "val_geracao": 40000 * k * daily * 0.6,
        }))
    d = RAW / "geracao"; d.mkdir(parents=True, exist_ok=True)
    pd.concat(grows).to_parquet(d / f"GERACAO_USINA-2_{y}.parquet", index=False)

    # cmo: semi-hourly
    hh = pd.date_range(idx[0], idx[-1], freq="30min")
    ss = season(hh)
    cmo = pd.concat([pd.DataFrame({
        "id_subsistema": sub, "nom_subsistema": SUBS[sub], "din_instante": hh,
        "val_cmo": (120 - 70 * ss).clip(15) * (1 + np.random.normal(0, .25, len(hh))),
    }) for sub in SCALE])
    d = RAW / "cmo"; d.mkdir(parents=True, exist_ok=True)
    cmo.to_parquet(d / f"CMO_SEMIHORARIO_{y}.parquet", index=False)

    # ena / ear: daily
    days = pd.date_range(idx[0].normalize(), idx[-1].normalize(), freq="D")
    ds = season(days)
    ena, ear, ear_ree = [], [], []
    for sub, k in SCALE.items():
        mlt = 30000 * k
        bruta = mlt * (1 + 0.55 * ds) * (1 + np.random.normal(0, .12, len(days)))
        ena.append(pd.DataFrame({
            "id_subsistema": sub, "nom_subsistema": SUBS[sub], "ena_data": days,
            "ena_bruta_regiao_mwmed": bruta,
            "ena_bruta_regiao_percentualmlt": 100 * bruta / mlt,
            "ena_armazenavel_regiao_mwmed": bruta * .8,
            "ena_armazenavel_regiao_percentualmlt": 100 * bruta * .8 / (mlt * .8),
        }))
        cap = 200000 * k
        pct = np.clip(55 + 22 * np.sin(2 * math.pi * (days.dayofyear.values - 110) / 365.0)
                      + np.random.normal(0, 1.2, len(days)), 8, 98)
        ear.append(pd.DataFrame({
            "id_subsistema": sub, "nom_subsistema": SUBS[sub], "ear_data": days,
            "ear_max_subsistema": cap, "ear_verif_subsistema_mwmes": cap * pct / 100,
            "ear_verif_subsistema_percentual": pct,
        }))
        # EAR by REE: mock hidraulico below sets nom_ree = the subsystem code
        # for every reservoir (a simplification -- real ONS has ~12 REEs that
        # don't map 1:1 to the 4 subsystems), so one mock REE per subsystem
        # here lines up with that and exercises the full ree_subsystem_map ->
        # agg_ear_ree path. Perturbed independently of the subsystem-level
        # `ear` series so the two are visibly distinct, not just duplicates.
        pct_ree = np.clip(pct + np.random.normal(0, 3, len(days)), 2, 100)
        ear_ree.append(pd.DataFrame({
            "nom_ree": sub, "ear_data": days,
            "ear_max_ree": cap, "ear_verif_ree_mwmes": cap * pct_ree / 100,
            "ear_verif_ree_percentual": pct_ree,
        }))
    for name, frames in (("ena", ena), ("ear", ear), ("ear_ree", ear_ree)):
        d = RAW / name; d.mkdir(parents=True, exist_ok=True)
        fn = {"ena": "ENA_DIARIO_SUBSISTEMA", "ear": "EAR_DIARIO_SUBSISTEMA",
              "ear_ree": "EAR_DIARIO_REE"}[name]
        pd.concat(frames).to_parquet(d / f"{fn}_{y}.parquet", index=False)
    print("mock", y, "written")

# --------------------------------------------------------------------------
# termica (per-plant programmed vs verified) and hidraulico (per-reservoir)
# --------------------------------------------------------------------------
PLANTS = []
for sub, k in SCALE.items():
    for fuel, share, n in [("Gás Natural", .45, 8), ("Carvão", .10, 3),
                           ("Óleo Diesel", .08, 4), ("Nuclear", .07, 2),
                           ("Biomassa", .20, 6), ("Óleo Combustível", .10, 3)]:
        for i in range(n):
            PLANTS.append((f"{fuel.split()[0].upper()} {sub}{i+1}", sub, fuel,
                           share * k / n))

BASINS = {"SE": ["GRANDE", "PARANA", "TOCANTINS", "DOCE", "AMAZONAS"],
          "S": ["IGUACU", "JACUI", "URUGUAI"], "NE": ["SAO FRANCISCO"],
          "N": ["TOCANTINS", "AMAZONAS"]}
RESERVOIRS = [(f"{b} RES {i+1}", sub, b)
              for sub, bs in BASINS.items() for b in bs for i in range(6)]

for y in YEARS:
    idx = hours(y); s = season(idx)
    hod = idx.hour.values
    daily = 1 + 0.12 * np.sin(2 * math.pi * (hod - 15) / 24.0)
    ter_shape = np.clip(0.16 - 0.08 * s, 0.03, None) * daily * 40000

    rows = []
    for nom, sub, fuel, w in PLANTS:
        prog = ter_shape * w * (1 + np.random.normal(0, .05, len(idx)))
        rows.append(pd.DataFrame({
            "din_instante": idx, "nom_tipopatamar": "Media",
            "id_subsistema": sub, "nom_subsistema": SUBS[sub], "nom_usina": nom,
            "cod_usinaplanejamento": nom[:6], "ceg": "X",
            "nom_tipocombustivel": fuel,
            "val_proggeracao": np.clip(prog, 0, None),
            "val_verifgeracao": np.clip(prog * (1 + np.random.normal(0, .07, len(idx))),
                                        0, None),
        }))
    d = RAW / "termica"; d.mkdir(parents=True, exist_ok=True)
    pd.concat(rows).to_parquet(d / f"GERACAO_TERMICA_DESPACHO-2_{y}.parquet", index=False)

    days = pd.date_range(idx[0].normalize(), idx[-1].normalize(), freq="D")
    ds = season(days)
    hrows = []
    for nom, sub, bacia in RESERVOIRS:
        base = 200 + abs(hash(nom)) % 700
        pct = np.clip(58 + 25 * np.sin(2 * math.pi * (days.dayofyear.values - 110) / 365)
                      + np.random.normal(0, 1.5, len(days)), 2, 100)
        hrows.append(pd.DataFrame({
            "din_instante": days, "id_subsistema": sub, "nom_subsistema": SUBS[sub],
            "tip_reservatorio": "Regularizacao", "nom_bacia": bacia,
            "nom_ree": sub, "id_reservatorio": abs(hash(nom)) % 9999,
            "nom_reservatorio": nom, "num_ordemcs": 1,
            "cod_usina": abs(hash(nom)) % 999,
            "val_nivelmontante": base + pct * 0.25,
            "val_niveljusante": base - 30,
            "val_volumeutilcon": pct,
            "val_vazaoafluente": 300 * (1 + ds) + np.random.normal(0, 20, len(days)),
            "val_vazaodefluente": 300 * (1 + ds),
            "val_vazaovertida": 0.0, "val_vazaotransferida": 0.0,
        }))
    d = RAW / "hidraulico"; d.mkdir(parents=True, exist_ok=True)
    pd.concat(hrows).to_parquet(d / f"DADOS_HIDROLOGICOS_RES_{y}.parquet", index=False)
    print("mock termica+hidraulico", y, "written")
