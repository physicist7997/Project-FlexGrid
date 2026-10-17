"""Synthetic but realistic district of homes for FlexLadder.

Every home gets its own random physical parameters (loads, devices, battery size, EV, ...)
AND the settings a homeowner would have entered in the FlexLadder homeowner app
(enrolled devices, comfort ceiling, EV departure + minimum charge, battery reserve,
never-touch list, preference mode, language, notification channel).

All values are illustrative simulation inputs. Addresses are synthetic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

# San Fernando Valley neighborhoods (approximate centers)
NEIGHBORHOODS = {
    "Van Nuys": (34.1866, -118.4487),
    "Reseda": (34.2011, -118.5365),
    "North Hollywood": (34.1870, -118.3813),
    "Panorama City": (34.2247, -118.4497),
    "Sylmar": (34.3078, -118.4492),
    "Pacoima": (34.2625, -118.4170),
    "Canoga Park": (34.2011, -118.5980),
    "Northridge": (34.2381, -118.5301),
    "Sherman Oaks": (34.1508, -118.4490),
    "Encino": (34.1591, -118.5012),
}
HALF_LAT, HALF_LON = 0.0165, 0.0215
QUADRANTS = [("North-West", 1, 0), ("North-East", 1, 1), ("South-West", 0, 0), ("South-East", 0, 1)]

STREETS = ["Sherman Way", "Victory Blvd", "Vanowen St", "Roscoe Blvd", "Saticoy St", "Sepulveda Blvd",
           "Van Nuys Blvd", "Reseda Blvd", "Tampa Ave", "Balboa Blvd", "Woodman Ave", "Laurel Canyon Blvd",
           "Lankershim Blvd", "Burbank Blvd", "Oxnard St", "Magnolia Blvd", "Chandler Blvd", "Nordhoff St",
           "Plummer St", "Devonshire St", "Parthenia St", "Lassen St", "Kittridge St", "Haynes St"]
TSTAT_BRANDS = ["ecobee", "Google Nest", "Honeywell Home"]
EV_MODELS = [("Tesla Model Y", 75), ("Tesla Model 3", 60), ("Chevy Bolt", 65), ("Hyundai Ioniq 5", 77),
             ("Ford Mustang Mach-E", 72), ("Nissan Leaf", 40), ("Kia EV6", 77)]
MEDICAL_DEVICES = ["Oxygen concentrator", "CPAP", "Home dialysis", "Powered wheelchair", "Refrigerated insulin"]
LANGUAGES = ["English", "Spanish", "Armenian", "Korean", "Tagalog", "Chinese", "Persian"]
LANG_P = [0.55, 0.30, 0.04, 0.03, 0.03, 0.03, 0.02]
MODES = ["Balanced", "Max savings", "Max comfort"]

CRITICAL_SITES = [
    # (neighborhood, quadrant index, name, kind, tier2_kw = pre-approved non-critical flexible load)
    ("Van Nuys", 2, "Valley Medical Center", "hospital", 450),
    ("North Hollywood", 0, "Fire Station 60 + 911 dispatch", "fire", 25),
    ("Sylmar", 1, "Sylmar water pumping plant", "water", 300),
    ("Canoga Park", 3, "West Valley Medical Center", "hospital", 500),
    ("Sherman Oaks", 1, "Sherman Oaks Rec Center (cooling center)", "cooling", 60),
    ("Panorama City", 2, "Panorama Senior Living", "senior", 80),
]
# Feeders reserved for automatic under-frequency relays (not in the manual rotation list)
UFLS = [("Reseda", 2), ("Pacoima", 1), ("Northridge", 3), ("Encino", 0)]


def _bounds(nb: str, q: int | None = None):
    """(south, north, west, east) of a neighborhood, or of one of its 4 feeder subsectors."""
    lat, lon = NEIGHBORHOODS[nb]
    s, n, w, e = lat - HALF_LAT, lat + HALF_LAT, lon - HALF_LON, lon + HALF_LON
    if q is None:
        return s, n, w, e
    _, north, east = QUADRANTS[q]
    mlat, mlon = (s + n) / 2, (w + e) / 2
    return (mlat if north else s), (n if north else mlat), (mlon if east else w), (e if east else mlon)


def build_feeders(rng: np.random.Generator, n_homes: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    i = 0
    for nb in NEIGHBORHOODS:
        for q, (qname, _, _) in enumerate(QUADRANTS):
            i += 1
            s, n, w, e = _bounds(nb, q)
            rows.append({"feeder": f"F{i:02d}", "name": f"{nb} {qname}", "neighborhood": nb, "quadrant": q,
                         "south": s, "north": n, "west": w, "east": e})
    f = pd.DataFrame(rows)
    raw = rng.integers(900, 1900, len(f)).astype(float)
    homes = np.floor(raw / raw.sum() * n_homes).astype(int)
    homes[: n_homes - homes.sum()] += 1
    f["homes"] = homes

    sites = []
    f["critical"] = False
    f["critical_site"] = ""
    f["tier2_kw"] = 0.0
    for nb, q, name, kind, kw in CRITICAL_SITES:
        idx = f.index[(f["neighborhood"] == nb) & (f["quadrant"] == q)][0]
        kw = round(kw * n_homes / C.N_HOMES, 1)          # site size scales with the district
        f.loc[idx, ["critical", "critical_site", "tier2_kw"]] = [True, name, float(kw)]
        r = f.loc[idx]
        sites.append({"site": name, "kind": kind, "feeder": r["feeder"], "neighborhood": nb, "tier2_kw": kw,
                      "lat": (r["south"] + r["north"]) / 2, "lon": (r["west"] + r["east"]) / 2})
    f["ufls"] = [(nb, q) in UFLS for nb, q in zip(f["neighborhood"], f["quadrant"])]
    f["sheddable"] = ~(f["critical"] | f["ufls"])
    order = rng.permutation(len(f))
    rank, ranks = 0, [None] * len(f)
    for j in order:
        if f.loc[j, "sheddable"]:
            ranks[j] = rank
            rank += 1
    f["rotation_rank"] = ranks
    return f, pd.DataFrame(sites)


def build_homes(f: pd.DataFrame, rng: np.random.Generator, enrollment: float, shares: dict | None = None) -> pd.DataFrame:
    sh = {"thermostat": C.SMART_TSTAT_SHARE_OF_AC, "ev": C.EV_SHARE, "water_heater": C.WH_ELECTRIC_SHARE,
          "pool": C.POOL_SHARE, "battery": C.BATTERY_SHARE, **(shares or {})}
    fe = np.repeat(f.index.to_numpy(), f["homes"].to_numpy())
    n = len(fe)
    R = lambda: rng.random(n)  # noqa: E731
    fr = f.loc[fe].reset_index(drop=True)

    # --- geography: homes along east-west streets inside the feeder subsector
    lat_span = fr["north"] - fr["south"]
    rows_per = 20
    row = rng.integers(1, rows_per, n)
    lat = fr["south"] + lat_span * row / rows_per + rng.normal(0, 0.00005, n)
    lon = fr["west"] + (fr["east"] - fr["west"]) * rng.uniform(0.03, 0.97, n)
    street = np.array(STREETS)[(row + fr["quadrant"].to_numpy() * 5 + fe * 3) % len(STREETS)]
    number = rng.integers(50, 220, n) * 100 + rng.integers(0, 99, n)

    has_ac = R() < C.AC_SHARE
    smart = has_ac & (R() < sh["thermostat"])
    ev = R() < sh["ev"]
    ev_model_i = rng.integers(0, len(EV_MODELS), n)
    wh = R() < sh["water_heater"]
    pool = R() < sh["pool"]
    batt = R() < sh["battery"]
    med = R() < C.MEDICAL_SHARE
    enrolled = R() < enrollment
    setpoint = rng.integers(73, 79, n)

    h = pd.DataFrame({
        "home_id": [f"H{k:05d}" for k in range(n)],
        "address": [f"{a} {b}" for a, b in zip(number, street)],
        "neighborhood": fr["neighborhood"].to_numpy(),
        "feeder": fr["feeder"].to_numpy(),
        "lat": lat.to_numpy(), "lon": lon,
        "sqft": np.clip(rng.lognormal(np.log(1600), 0.3, n), 650, 5000).round(-1).astype(int),
        "residents": rng.integers(1, 6, n),
        # ---- physical loads
        "base_kw": np.clip(rng.lognormal(np.log(C.BASE_KW_MEAN), 0.35, n), 0.3, 4.0).round(2),
        "has_ac": has_ac,
        "ac_kw": np.where(has_ac, rng.uniform(*C.AC_KW_RANGE, n), 0.0).round(2),
        "tstat_brand": np.where(smart, np.array(TSTAT_BRANDS)[rng.integers(0, 3, n)], "none"),
        "setpoint_f": setpoint,
        "ev": ev,
        "ev_model": np.where(ev, np.array([m for m, _ in EV_MODELS])[ev_model_i], ""),
        "ev_battery_kwh": np.where(ev, np.array([k for _, k in EV_MODELS])[ev_model_i], 0),
        "ev_charger_kw": np.where(ev, rng.choice([7.2, 11.5, 1.4], n, p=[0.7, 0.15, 0.15]), 0.0),
        "ev_soc_pct": np.where(ev, rng.integers(25, 80, n), 0),
        "ev_plug_hour": np.where(ev, np.clip(rng.normal(18.2, 1.3, n), 15, 23), 0).round(2),
        "ev_schedule": np.where(ev, rng.choice(["Off-peak (after 9 PM)", "Immediately on plug-in"], n,
                                               p=[0.65, 0.35]), ""),
        "water_heater": np.where(wh, "Electric (connected)", "Gas / not controllable"),
        "wh_kw": np.where(wh, 4.5, 0.0),
        "pool_pump": pool,
        "pool_runs_evening": pool & (R() < 0.35),
        "solar_kw": np.where(R() < 0.25, rng.uniform(4, 9, n), 0).round(1),
        "battery": batt,
        "battery_kwh": np.where(batt, rng.choice([25.0, 39.2, 50.0], n, p=[0.5, 0.4, 0.1]), 0.0),
        "battery_inverter_kw": np.where(batt, 11.0, 0.0),
        "battery_soc_pct": np.where(batt, rng.integers(80, 96, n), 0),
        "medical": med,
        "medical_device": np.where(med, np.array(MEDICAL_DEVICES)[rng.integers(0, len(MEDICAL_DEVICES), n)], ""),
        # ---- connectivity
        "comms": rng.choice(["Wi-Fi", "Cellular"], n, p=[0.85, 0.15]),
        "telemetry_age_s": np.where(R() < C.DEFAULT_STALE_FRAC, rng.integers(181, 3600, n), rng.integers(2, 60, n)),
        # ---- homeowner-app settings
        "enrolled": enrolled,
        "enroll_ev": enrolled & ev & (R() < 0.9),
        "enroll_thermostat": enrolled & smart & (R() < 0.9),
        "enroll_water_heater": enrolled & wh & (R() < 0.9),
        "enroll_pool": enrolled & pool & (R() < 0.9),
        "battery_participates": batt & (R() < 0.95),
        "comfort_max_f": np.minimum(setpoint + rng.choice([1, 2, 3, 4], n, p=[0.15, 0.35, 0.35, 0.15]), 82),
        "ev_depart_hour": np.where(ev, rng.choice([6.0, 6.5, 7.0, 7.5, 8.0, 9.0], n), 0.0),
        "ev_min_charge_pct": np.where(ev, rng.choice([60, 70, 80, 90], n, p=[0.15, 0.3, 0.4, 0.15]), 0),
        "battery_reserve_pct": np.where(batt, rng.choice([20, 40, 60], n, p=[0.5, 0.35, 0.15]), 0),
        "mode": rng.choice(MODES, n, p=[0.6, 0.2, 0.2]),
        "language": rng.choice(LANGUAGES, n, p=LANG_P),
        "notify": rng.choice(["SMS", "Email"], n, p=[0.8, 0.2]),
        "events_this_season": rng.poisson(4, n),
        # ---- fixed randomness for device on/off states (reproducible)
        "u_ac": R(), "u_wh": R(), "u_override": R(), "u_stale": R(),
    })
    # never-touch lists entered by homeowners
    nt_t = enrolled & (R() < 0.08)
    nt_e = enrolled & (R() < 0.05)
    h["never_touch"] = [", ".join(x for x, f_ in (("Thermostat", a), ("EV", b)) if f_) for a, b in zip(nt_t, nt_e)]
    h.loc[nt_t, "enroll_thermostat"] = False
    h.loc[nt_e, "enroll_ev"] = False
    h.loc[med, "enroll_thermostat"] = False   # conservative: medical homes' HVAC never nudged
    return h


def build_district(seed: int = C.SEED, n_homes: int = C.N_HOMES, enrollment: float = C.DEFAULT_ENROLLMENT,
                   shares: dict | None = None):
    rng = np.random.default_rng(seed)
    feeders, sites = build_feeders(rng, n_homes)
    homes = build_homes(feeders, rng, enrollment, shares)
    return {"feeders": feeders, "sites": sites, "homes": homes, "seed": seed, "enrollment": enrollment,
            "shares": shares or {}}


def polygons(feeders: pd.DataFrame):
    """GeoJSON-like polygons for neighborhoods and feeders."""
    nbs = []
    for nb in NEIGHBORHOODS:
        s, n, w, e = _bounds(nb)
        nbs.append({"name": nb, "bounds": [[s, w], [n, e]],
                    "poly": [[s, w], [n, w], [n, e], [s, e]], "center": NEIGHBORHOODS[nb]})
    fds = [{"feeder": r.feeder, "name": r.name, "neighborhood": r.neighborhood,
            "bounds": [[r.south, r.west], [r.north, r.east]],
            "poly": [[r.south, r.west], [r.north, r.west], [r.north, r.east], [r.south, r.east]]}
           for r in feeders.itertuples()]
    return nbs, fds


if __name__ == "__main__":
    import time
    t = time.time()
    d = build_district()
    h = d["homes"]
    print(f"{len(h):,} homes, {len(d['feeders'])} feeders, {len(d['sites'])} critical sites in {time.time() - t:.2f}s")
    print(h.iloc[0].to_dict())
    print("enrolled", h.enrolled.mean().round(3), "ev", h.ev.mean().round(3), "batt", h.battery.mean().round(3))
