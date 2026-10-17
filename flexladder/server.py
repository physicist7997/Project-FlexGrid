"""FlexLadder API + static dashboard.  Run:  uvicorn server:app --reload   then open http://localhost:8000

Single-user demo server: the district, failures and last plan live in memory.
"""
from __future__ import annotations

import io
import random
import time
from dataclasses import asdict

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config as C
from dataset import build_district, polygons
from planner import ACTIONS, LABEL, Failures, Request, explain_home, fmt_clock, plan

app = FastAPI(title="FlexLadder")
app.mount("/static", StaticFiles(directory="static"), name="static")

S: dict = {}


def _req_from_template(key: str) -> Request:
    tp = C.TEMPLATES[key]
    return Request(**{k: v for k, v in tp.items() if k != "why"})


def _reset(seed: int = C.SEED, n_homes: int = C.N_HOMES, enrollment: float = C.DEFAULT_ENROLLMENT,
           shares: dict | None = None):
    S["d"] = build_district(seed, n_homes, enrollment, shares)
    S["fl"] = Failures()
    S["req"] = S.get("req") or _req_from_template(C.DEFAULT_TEMPLATE)
    S["events"] = []
    _replan()


def _replan():
    t = time.perf_counter()
    S["plan"] = plan(S["d"], S["req"], S["fl"])
    S["plan"]["ms"] = round(1000 * (time.perf_counter() - t))
    return S["plan"]


def _clean(x):
    """Make anything JSON-safe: numpy scalars -> python, NaN -> None."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _light(p: dict) -> dict:
    return _clean(_light_raw(p))


def _light_raw(p: dict) -> dict:
    """Plan without DataFrames, for JSON."""
    f = S["d"]["feeders"]
    ivs = []
    for iv in p["intervals"]:
        ivs.append({k: v for k, v in iv.items() if k != "taken"} | {"label": fmt_clock(iv["clock"])})
    return {"request": p["request"], "failures": p["failures"], "summary": p["summary"], "intervals": ivs,
            "trace": S["events"] + p["trace"], "ms": p.get("ms"),
            "feeder_meta": f[["feeder", "name", "neighborhood", "homes", "critical", "critical_site", "ufls",
                              "sheddable", "rotation_rank"]].to_dict("records"),
            "dataset": _dataset_info()}


def _dataset_info() -> dict:
    h = S["d"]["homes"]
    return {"seed": S["d"]["seed"], "homes": len(h), "enrollment": float(h["enrolled"].mean()),
            "enrolled": int(h["enrolled"].sum()), "batteries": int(h["battery"].sum()), "evs": int(h["ev"].sum()),
            "thermostats": int((h["tstat_brand"] != "none").sum()), "water_heaters": int((h["wh_kw"] > 0).sum()),
            "pools": int(h["pool_pump"].sum()), "medical": int(h["medical"].sum()),
            "critical_sites": len(S["d"]["sites"]), "feeders": len(S["d"]["feeders"])}


@app.on_event("startup")
def startup():
    _reset()


# ------------------------------------------------------------------ pages
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")


@app.get("/simple", response_class=HTMLResponse)
def simple_page():
    return FileResponse("static/simple.html")


@app.get("/homeowner", response_class=HTMLResponse)
def homeowner_page():
    return FileResponse("static/homeowner.html")


# ------------------------------------------------------------------ reference data
@app.get("/api/geo")
def geo():
    nbs, fds = polygons(S["d"]["feeders"])
    return _clean({"neighborhoods": nbs, "feeders": fds, "sites": S["d"]["sites"].to_dict("records")})


@app.get("/api/templates")
def templates():
    return {"templates": C.TEMPLATES, "default": C.DEFAULT_TEMPLATE,
            "neighborhoods": list(S["d"]["feeders"]["neighborhood"].unique()),
            "brands": ["ecobee", "Google Nest", "Honeywell Home"]}


@app.get("/api/plan")
def get_plan():
    return _light(S["plan"])


class PlanIn(BaseModel):
    label: str = "Custom dispatch"
    target_mw: float
    start_hour: float
    duration_h: float
    deadline_min: int
    kind: str = "routine"
    area: list[str] = []


@app.post("/api/plan")
def post_plan(body: PlanIn):
    S["req"] = Request(**body.model_dump())
    S["events"] = []
    return _light(_replan())


# ------------------------------------------------------------------ failures
class FailIn(BaseModel):
    type: str
    neighborhood: str | None = None
    brand: str | None = None
    frac: float | None = None


@app.post("/api/failure")
def failure(body: FailIn):
    fl: Failures = S["fl"]
    before = S["plan"]["summary"]
    nbs = list(S["d"]["feeders"]["neighborhood"].unique())
    if body.type == "comms_outage":
        nb = body.neighborhood or random.choice([n for n in nbs if n not in fl.comms_out_neighborhoods] or nbs)
        fl.comms_out_neighborhoods = sorted(set(fl.comms_out_neighborhoods) | {nb})
        what = f"Cell/ISP outage in {nb}: devices there stop reporting"
    elif body.type == "thermostat_cloud":
        b = body.brand or random.choice(["ecobee", "Google Nest", "Honeywell Home"])
        fl.thermostat_cloud_down = sorted(set(fl.thermostat_cloud_down) | {b})
        what = f"{b} thermostat cloud outage"
    elif body.type == "battery_api":
        fl.battery_api_down = True
        what = "Partner battery fleet API unreachable"
    elif body.type == "overrides":
        fl.override_frac = min(1.0, fl.override_frac + (body.frac or 0.25))
        fl.override_from_h = 1.0
        what = f"Override wave: {fl.override_frac:.0%} of thermostat participants opt out after the first hour"
    elif body.type == "dropouts":
        fl.telemetry_dropout_frac = min(1.0, fl.telemetry_dropout_frac + (body.frac or 0.10))
        what = f"Random telemetry dropouts: {fl.telemetry_dropout_frac:.0%} of devices silent"
    elif body.type == "hotter":
        fl.heat_bump = min(0.3, fl.heat_bump + 0.06)
        fl.extra_target_mw += 2.0
        what = f"Hotter than forecast: more AC running, need +2 MW (total +{fl.extra_target_mw:g} MW)"
    else:
        raise HTTPException(400, "unknown failure type")
    p = _replan()
    after = p["summary"]
    S["events"].insert(0, {"step": f"⚠ Failure injected @ {time.strftime('%H:%M:%S')}", "detail": what})
    S["events"].insert(1, {"step": "↻ Re-planned", "detail":
                           f"First-hour relief {before['first_mw']:.2f} → {after['first_mw']:.2f} MW · worst shortfall "
                           f"{before['max_short_mw']:.2f} → {after['max_short_mw']:.2f} MW · homes dark at peak "
                           f"{before['peak_dark_flex']:,} → {after['peak_dark_flex']:,} (in {p['ms']} ms)."})
    return _light(p)


@app.post("/api/failures/reset")
def reset_failures():
    S["fl"] = Failures()
    S["events"] = [{"step": "Failures cleared", "detail": "All devices and APIs restored."}]
    return _light(_replan())


# ------------------------------------------------------------------ dataset
class DatasetIn(BaseModel):
    seed: int | None = None
    n_homes: int = C.N_HOMES
    enrollment: float = C.DEFAULT_ENROLLMENT
    battery_share: float | None = None
    ev_share: float | None = None
    thermostat_share: float | None = None   # share of AC homes with a smart thermostat
    water_heater_share: float | None = None
    pool_share: float | None = None


@app.post("/api/dataset")
def regenerate(body: DatasetIn):
    seed = body.seed if body.seed is not None else random.randint(1, 99999)
    shares = {k: float(np.clip(v, 0, 1)) for k, v in {
        "battery": body.battery_share, "ev": body.ev_share, "thermostat": body.thermostat_share,
        "water_heater": body.water_heater_share, "pool": body.pool_share}.items() if v is not None}
    _reset(seed, int(np.clip(body.n_homes, 1000, 120000)), float(np.clip(body.enrollment, 0, 1)), shares)
    S["events"] = [{"step": "New dataset", "detail": f"Generated {len(S['d']['homes']):,} homes (seed {seed}, "
                    f"{body.enrollment:.0%} target enrollment)."}]
    return _light(S["plan"])


# ------------------------------------------------------------------ homes & search
def _status(k: int) -> pd.Series:
    h = S["d"]["homes"]
    taken = S["plan"]["intervals"][min(k, len(S["plan"]["intervals"]) - 1)]["taken"]
    st = pd.Series("not_enrolled", index=h.index)
    st[h["enrolled"].to_numpy()] = "enrolled"
    fl = S["fl"]
    stale = ((h["telemetry_age_s"] > C.STALE_THRESHOLD_S) | h["neighborhood"].isin(fl.comms_out_neighborhoods)
             | (h["u_stale"] < fl.telemetry_dropout_frac))
    st[stale & h["enrolled"]] = "silent"
    st[taken.loc[taken["home_idx"] >= 0, "home_idx"].unique()] = "dispatched"
    return st


def _relief(k: int) -> pd.Series:
    taken = S["plan"]["intervals"][min(k, len(S["plan"]["intervals"]) - 1)]["taken"]
    return taken[taken["home_idx"] >= 0].groupby("home_idx")["kw"].sum()


@app.get("/api/homes")
def homes_in_feeder(feeder: str, k: int = 0):
    h = S["d"]["homes"]
    m = (h["feeder"] == feeder).to_numpy()
    st = _status(k)[m]
    rel = _relief(k).reindex(h.index[m]).fillna(0)
    sub = h[m]
    return {"homes": [{"id": i, "lat": round(a, 6), "lon": round(b, 6), "s": s, "m": bool(md), "b": bool(bt),
                       "e": bool(ev), "r": round(float(r), 2)}
                      for i, a, b, s, md, bt, ev, r in zip(sub["home_id"], sub["lat"], sub["lon"], st, sub["medical"],
                                                         sub["battery"], sub["ev"], rel)]}


@app.get("/api/home/{home_id}")
def home(home_id: str, k: int = 0):
    h = S["d"]["homes"]
    if home_id not in set(h["home_id"]):
        raise HTTPException(404, "home not found")
    k = min(k, len(S["plan"]["intervals"]) - 1)
    rec = explain_home(S["d"], home_id, S["req"], S["fl"], k, S["plan"]["intervals"][k]["taken"])
    rec["status"] = _status(k)[int(home_id[1:])]
    return _clean(rec)


@app.get("/api/random_home")
def random_home(neighborhood: str | None = None, feeder: str | None = None, dispatched: bool = False, k: int = 0):
    h = S["d"]["homes"]
    m = pd.Series(True, index=h.index)
    if neighborhood:
        m &= h["neighborhood"] == neighborhood
    if feeder:
        m &= h["feeder"] == feeder
    if dispatched:
        m &= _status(k) == "dispatched"
    ids = h.loc[m, "home_id"]
    if ids.empty:
        raise HTTPException(404, "no matching home")
    return {"id": random.choice(ids.tolist())}


@app.get("/api/search")
def search(q: str = "", neighborhood: str = "", feeder: str = "", filters: str = "", k: int = 0, limit: int = 200):
    h = S["d"]["homes"]
    st = _status(k)
    rel = _relief(k).reindex(h.index).fillna(0)
    m = pd.Series(True, index=h.index)
    if q:
        ql = q.lower()
        m &= h["home_id"].str.lower().str.contains(ql) | h["address"].str.lower().str.contains(ql)
    if neighborhood:
        m &= h["neighborhood"] == neighborhood
    if feeder:
        m &= h["feeder"] == feeder
    for flt in [x for x in filters.split(",") if x]:
        m &= {"enrolled": h["enrolled"], "battery": h["battery"], "ev": h["ev"], "medical": h["medical"],
              "pool": h["pool_pump"], "dispatched": st == "dispatched", "silent": st == "silent",
              "thermostat": h["tstat_brand"] != "none"}.get(flt, True)
    sub = h[m].head(limit)
    rows = [{"id": r.home_id, "address": r.address, "neighborhood": r.neighborhood, "feeder": r.feeder,
             "status": st[i], "relief_kw": round(float(rel[i]), 2),
             "devices": " ".join(x for x, f_ in (("🔋", r.battery), ("🚗", r.ev), ("🌡", r.tstat_brand != "none"),
                                                  ("🚿", r.wh_kw > 0), ("🏊", r.pool_pump), ("⚕", r.medical)) if f_)}
            for i, r in zip(sub.index, sub.itertuples())]
    return _clean({"total": int(m.sum()), "rows": rows})


@app.get("/api/feeders")
def feeders_table(k: int = 0):
    f = S["d"]["feeders"]
    iv = S["plan"]["intervals"][min(k, len(S["plan"]["intervals"]) - 1)]
    st = _status(k)
    h = S["d"]["homes"]
    disp = st[st == "dispatched"].groupby(h["feeder"]).size()
    rows = []
    for r in f.itertuples():
        share = iv["shares"].get(r.feeder, 0)
        got = iv["by_feeder"].get(r.feeder, 0)
        rows.append({"feeder": r.feeder, "name": r.name, "neighborhood": r.neighborhood, "homes": int(r.homes),
                     "load_mw": round(iv["feeder_load"].get(r.feeder, 0) / 1000, 2),
                     "share_kw": round(share), "flex_kw": round(got), "homes_dispatched": int(disp.get(r.feeder, 0)),
                     "status": "OPEN (dark)" if r.feeder in iv["flex_open"] else
                     ("paid" if share and got >= share - 1 else ("partial" if got else ("—" if not share else "none"))),
                     "critical": r.critical_site or ("Relay-reserved" if r.ufls else ""),
                     "rotation_rank": r.rotation_rank})
    return _clean({"rows": rows})


# ------------------------------------------------------------------ homeowner app
HOMEOWNER_FIELDS = ["enrolled", "enroll_ev", "enroll_thermostat", "enroll_water_heater", "enroll_pool",
                    "battery_participates", "comfort_max_f", "ev_depart_hour", "ev_min_charge_pct",
                    "battery_reserve_pct", "mode", "language", "notify", "never_touch"]


@app.get("/api/homeowner/{home_id}")
def homeowner_get(home_id: str):
    return home(home_id, 0)


@app.post("/api/homeowner/{home_id}")
def homeowner_set(home_id: str, body: dict):
    h = S["d"]["homes"]
    if home_id not in set(h["home_id"]):
        raise HTTPException(404, "home not found")
    i = int(home_id[1:])
    for k, v in body.items():
        if k in HOMEOWNER_FIELDS:
            h.at[i, k] = v
    # consistency: device enrollment requires owning the device and being enrolled
    if not h.at[i, "enrolled"]:
        for k in ["enroll_ev", "enroll_thermostat", "enroll_water_heater", "enroll_pool"]:
            h.at[i, k] = False
    h.at[i, "enroll_ev"] = bool(h.at[i, "enroll_ev"] and h.at[i, "ev"] and "EV" not in h.at[i, "never_touch"])
    h.at[i, "enroll_thermostat"] = bool(h.at[i, "enroll_thermostat"] and h.at[i, "tstat_brand"] != "none"
                                        and not h.at[i, "medical"] and "Thermostat" not in h.at[i, "never_touch"])
    h.at[i, "enroll_water_heater"] = bool(h.at[i, "enroll_water_heater"] and h.at[i, "wh_kw"] > 0)
    h.at[i, "enroll_pool"] = bool(h.at[i, "enroll_pool"] and h.at[i, "pool_pump"])
    h.at[i, "battery_participates"] = bool(h.at[i, "battery_participates"] and h.at[i, "battery"])
    S["events"].insert(0, {"step": f"Homeowner update {home_id}", "detail": "Preferences changed in the homeowner app; plan recomputed."})
    _replan()
    return home(home_id, 0)


# ------------------------------------------------------------------ export
@app.get("/api/export.csv")
def export(k: int = 0):
    h = S["d"]["homes"]
    iv = S["plan"]["intervals"][min(k, len(S["plan"]["intervals"]) - 1)]
    t = iv["taken"]
    t = t[t["home_idx"] >= 0]
    out = pd.DataFrame({"interval": iv["label"] if "label" in iv else fmt_clock(iv["clock"]),
                        "home_id": h.loc[t["home_idx"], "home_id"].to_numpy(),
                        "address": h.loc[t["home_idx"], "address"].to_numpy(),
                        "feeder": h.loc[t["home_idx"], "feeder"].to_numpy(),
                        "action": t["action"].map(LABEL).to_numpy(), "kw": t["kw"].round(2).to_numpy()})
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=flexladder_dispatch_{k}.csv"})
