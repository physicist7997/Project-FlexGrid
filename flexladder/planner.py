"""FlexGrid planner: turns an operator request into an exact, home-by-home dispatch plan.

Per 15-minute interval:
  1. State      - which devices are actually running right now (AC, EV charging, water heater, pool)
  2. Eligibility- homeowner consent + limits, telemetry freshness, response deadline, failures
  3. Candidates - every (home, action, kW) that could help, with a disruption cost
  4. Allocation - every feeder in the area pays its pro-rata share, least-disruptive first;
                  leftovers anywhere in the area cover the rest; fairness cap per home
  5. Fallback   - emergency only: shortfall goes to the utility's feeder rotation list
  6. Old way    - counterfactual: rotation list only, no flexibility

Deterministic. No LLM in the control loop; the trace explains every step.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

import config as C

ACTIONS = ["ev", "pool", "water_heater", "battery", "tier2_site", "thermostat"]
LABEL = {
    "ev": "Pause EV charging", "pool": "Pause pool pump", "water_heater": "Delay water heater",
    "battery": "Partner battery discharge", "tier2_site": "Critical-site Tier-2 trim",
    "thermostat": "Thermostat nudge (+°F)",
}
WEIGHT = {"ev": 1.0, "pool": 1.2, "water_heater": 1.5, "battery": 2.0, "tier2_site": 2.5, "thermostat": 3.0}
LATENCY_MIN = {"battery": 1, "ev": 2, "pool": 5, "water_heater": 5, "thermostat": 5, "tier2_site": 10}
CONF = {"ev": 0.95, "pool": 0.90, "water_heater": 0.90, "battery": 0.95, "tier2_site": 0.90, "thermostat": 0.80}
KW_PER_DEGF = 0.35                     # ~0.7-1.0 kW for 2-3 °F (SMUD/Nest first-hour results)
TSTAT_DECAY = [1.00, 0.55, 0.30, 0.22]  # by elapsed hour (Reliant/NRG Texas: ~22% by hour 4)
MODE_OFFSET = {"Max savings": 3, "Balanced": 2, "Max comfort": 1}
MODE_MULT = {"Max savings": 0.8, "Balanced": 1.0, "Max comfort": 1.6}
STALE_S = C.STALE_THRESHOLD_S
FAIR_CAP_KW = C.FAIRNESS_CAP_KW
COMMITTED = C.BATTERY_COMMITTED_FRAC
VOLL_RES = 3964   # $/MWh, Brattle 2024 ERCOT residential, 1-hour outage


@dataclass
class Request:
    label: str = "Custom dispatch"
    target_mw: float = 6.0
    start_hour: float = 17.0
    duration_h: float = 2.0
    deadline_min: int = 30
    kind: str = "routine"                         # "routine" never opens breakers; "emergency" may
    area: list[str] = field(default_factory=list)  # neighborhoods; empty = whole district


@dataclass
class Failures:
    comms_out_neighborhoods: list[str] = field(default_factory=list)
    thermostat_cloud_down: list[str] = field(default_factory=list)
    battery_api_down: bool = False
    override_frac: float = 0.0
    override_from_h: float = 1.0
    telemetry_dropout_frac: float = 0.0
    heat_bump: float = 0.0
    extra_target_mw: float = 0.0
    log: list[str] = field(default_factory=list)

    def active(self) -> list[str]:
        out = []
        if self.comms_out_neighborhoods:
            out.append(f"Comms outage: {', '.join(self.comms_out_neighborhoods)}")
        if self.thermostat_cloud_down:
            out.append(f"Thermostat cloud down: {', '.join(self.thermostat_cloud_down)}")
        if self.battery_api_down:
            out.append("Partner battery API down")
        if self.override_frac:
            out.append(f"{self.override_frac:.0%} of thermostat participants override after {self.override_from_h:g} h")
        if self.telemetry_dropout_frac:
            out.append(f"{self.telemetry_dropout_frac:.0%} random telemetry dropouts")
        if self.heat_bump:
            out.append(f"Hotter than forecast (+{self.heat_bump:.0%} AC running, +{self.extra_target_mw:g} MW needed)")
        return out


# ------------------------------------------------------------------ 1. device state at clock time t
def device_state(h: pd.DataFrame, t: float, k: int, fl: Failures) -> dict:
    p_ac = C.AC_ON_PROB.get(int(t), 0.5) + fl.heat_bump
    ac_on = h["has_ac"].to_numpy() & (h["u_ac"].to_numpy() < p_ac)
    ev = h["ev"].to_numpy()
    immediate = h["ev_schedule"].to_numpy() == "Immediately on plug-in"
    plug = h["ev_plug_hour"].to_numpy()
    start = np.where(immediate, plug, np.maximum(plug, 21.0))
    tgt = np.maximum(h["ev_min_charge_pct"].to_numpy(), 80)
    need_kwh = np.clip(tgt - h["ev_soc_pct"].to_numpy(), 0, None) / 100 * h["ev_battery_kwh"].to_numpy()
    kw = np.where(ev, h["ev_charger_kw"].to_numpy(), 1.0)
    need_h = need_kwh / kw
    ev_charging = ev & (start <= t) & (t < start + need_h)
    wh_on = (h["wh_kw"].to_numpy() > 0) & (((h["u_wh"].to_numpy() + k * 0.37) % 1.0) < C.WH_ACTIVE_PROB)
    pool_on = h["pool_runs_evening"].to_numpy() & (t < 20.5)
    load = (h["base_kw"].to_numpy() + h["ac_kw"].to_numpy() * ac_on + np.where(ev_charging, kw, 0)
            + h["wh_kw"].to_numpy() * wh_on + C.POOL_KW * pool_on)
    return {"ac_on": ac_on, "ev_charging": ev_charging, "ev_start": start, "ev_need_h": need_h,
            "wh_on": wh_on, "pool_on": pool_on, "load": load}


# ------------------------------------------------------------------ 2-3. candidates (+ reasons for one home)
def candidates(h: pd.DataFrame, st: dict, t: float, elapsed: float, req: Request, fl: Failures,
               explain: bool = False):
    n = len(h)
    end = req.start_hour + req.duration_h
    nb = h["neighborhood"].to_numpy()
    stale = ((h["telemetry_age_s"].to_numpy() > STALE_S)
             | np.isin(nb, fl.comms_out_neighborhoods)
             | (h["u_stale"].to_numpy() < fl.telemetry_dropout_frac))
    fresh = ~stale
    in_area = np.isin(nb, req.area) if req.area else np.ones(n, bool)
    ok_dl = {a: LATENCY_MIN[a] <= req.deadline_min for a in ACTIONS}
    kw = {}
    why = {a: np.full(n, "", dtype=object) for a in ACTIONS} if explain else None

    def note(a, mask, text):
        if explain:
            sel = mask & (why[a] == "")
            why[a][sel] = text

    # EV: pause only if the homeowner's departure charge is still reachable
    depart = 24 + h["ev_depart_hour"].to_numpy()
    min_kwh = np.clip(h["ev_min_charge_pct"].to_numpy() - h["ev_soc_pct"].to_numpy(), 0, None) / 100 \
        * h["ev_battery_kwh"].to_numpy()
    window_kwh = np.clip(depart - end, 0, None) * h["ev_charger_kw"].to_numpy()
    ev_ok = window_kwh >= 1.1 * min_kwh
    e = h["enroll_ev"].to_numpy()
    if explain:
        note("ev", ~h["ev"].to_numpy(), "No EV")
        note("ev", ~e, "EV not enrolled / never-touch")
        note("ev", ~st["ev_charging"], "Not charging right now")
        note("ev", ~ev_ok, "Pause would miss homeowner's departure charge")
    kw["ev"] = np.where(e & st["ev_charging"] & ev_ok, h["ev_charger_kw"].to_numpy() * CONF["ev"], 0.0)

    # Pool pump: pause up to 2 h
    p = h["enroll_pool"].to_numpy()
    if explain:
        note("pool", ~h["pool_pump"].to_numpy(), "No pool pump")
        note("pool", ~p, "Pool pump not enrolled")
        note("pool", ~st["pool_on"], "Not running now")
        note("pool", np.full(n, elapsed >= C.POOL_MAX_PAUSE_H), "Already paused 2 h (filtration limit)")
    kw["pool"] = np.where(p & st["pool_on"] & (elapsed < C.POOL_MAX_PAUSE_H), C.POOL_KW * CONF["pool"], 0.0)

    # Water heater: delay up to 60 min
    w = h["enroll_water_heater"].to_numpy()
    if explain:
        note("water_heater", h["wh_kw"].to_numpy() == 0, "Gas / not controllable")
        note("water_heater", ~w, "Water heater not enrolled")
        note("water_heater", ~st["wh_on"], "Not heating now")
        note("water_heater", np.full(n, elapsed >= C.WH_MAX_DELAY_H), "Already delayed 60 min (hot-water limit)")
    kw["water_heater"] = np.where(w & st["wh_on"] & (elapsed < C.WH_MAX_DELAY_H),
                                  h["wh_kw"].to_numpy() * CONF["water_heater"], 0.0)

    # Thermostat: homeowner comfort ceiling + preference mode; decays over the event
    th = h["enroll_thermostat"].to_numpy()
    brand_down = np.isin(h["tstat_brand"].to_numpy(), fl.thermostat_cloud_down)
    overridden = (elapsed >= fl.override_from_h) & (h["u_override"].to_numpy() < fl.override_frac)
    offset = np.minimum(h["comfort_max_f"].to_numpy() - h["setpoint_f"].to_numpy(),
                        h["mode"].map(MODE_OFFSET).to_numpy())
    decay = TSTAT_DECAY[min(int(elapsed), len(TSTAT_DECAY) - 1)]
    th_kw = np.minimum(offset * KW_PER_DEGF, h["ac_kw"].to_numpy() * 0.4) * decay * CONF["thermostat"]
    if explain:
        note("thermostat", h["tstat_brand"].to_numpy() == "none", "No smart thermostat")
        note("thermostat", h["medical"].to_numpy(), "Medical home: HVAC protected")
        note("thermostat", ~th, "Thermostat not enrolled / never-touch")
        note("thermostat", ~st["ac_on"], "AC not running now")
        note("thermostat", brand_down, "Thermostat cloud outage")
        note("thermostat", overridden, "Homeowner overrode this event")
        note("thermostat", offset < 1, "Already at homeowner's comfort ceiling")
    kw["thermostat"] = np.where(th & st["ac_on"] & ~brand_down & ~overridden & (offset >= 1), th_kw, 0.0)

    # Partner battery: only the uncommitted share, never below the homeowner's reserve
    b = h["battery_participates"].to_numpy() & ~fl.battery_api_down
    energy = np.clip(h["battery_soc_pct"].to_numpy() - h["battery_reserve_pct"].to_numpy(), 0, None) / 100 \
        * h["battery_kwh"].to_numpy() * 0.9
    b_kw = np.minimum(h["battery_inverter_kw"].to_numpy() * (1 - COMMITTED), energy / max(req.duration_h, 0.25))
    if explain:
        note("battery", ~h["battery"].to_numpy(), "No battery")
        note("battery", np.full(n, fl.battery_api_down), "Partner battery API down")
        note("battery", ~h["battery_participates"].to_numpy(), "Homeowner opted battery out")
    kw["battery"] = np.where(b, b_kw * CONF["battery"], 0.0)

    for a in kw:
        if not ok_dl[a]:
            note(a, np.ones(n, bool), f"Too slow for a {req.deadline_min}-min deadline")
            kw[a] = kw[a] * 0
        note(a, stale, f"Not reporting (telemetry > {STALE_S}s or comms outage)")
        note(a, ~in_area, "Outside requested area")
        kw[a] = np.where(fresh & in_area, kw[a], 0.0)
    return kw, why, {"stale": stale, "in_area": in_area, "ev_blocked": h["ev"].to_numpy() & e & st["ev_charging"] & ~ev_ok}


def allocate(cand: pd.DataFrame, shares: pd.Series) -> pd.DataFrame:
    """cand: rows (home_idx, feeder, action, kw, cost). Whole devices only. Returns taken rows."""
    if cand.empty:
        return cand.assign(take=False)
    cand = cand.sort_values(["home_idx", "cost"])
    nb = cand["action"] != "battery"
    cum_home = cand["kw"].where(nb, 0).groupby(cand["home_idx"]).cumsum()
    cand = cand[~nb | (cum_home - cand["kw"] < FAIR_CAP_KW)]          # fairness cap per home
    cand = cand.sort_values(["feeder", "cost"])
    cum = cand.groupby("feeder")["kw"].cumsum()
    take1 = (cum - cand["kw"]) < cand["feeder"].map(shares).fillna(0)
    taken = cand[take1]
    remaining = float((shares - taken.groupby("feeder")["kw"].sum().reindex(shares.index).fillna(0))
                      .clip(lower=0).sum())
    rest = cand[~take1].sort_values("cost")
    take2 = (rest["kw"].cumsum() - rest["kw"]) < remaining
    out = pd.concat([taken, rest[take2]])
    # trim overshoot: drop the most disruptive actions while the target is still met
    target = float(shares.sum())
    out = out.sort_values("cost", ascending=False)
    surplus = float(out["kw"].sum()) - target
    drop = out["kw"].cumsum() <= surplus
    return out[~drop]


def rotation_open(f: pd.DataFrame, area_feeders: list[str], net_kw: pd.Series, need_kw: float, ptr: int):
    if need_kw <= 1e-6:
        return []
    rot = f[f["sheddable"] & f["feeder"].isin(area_feeders)].sort_values("rotation_rank")["feeder"].tolist()
    out, got = [], 0.0
    for i in range(len(rot)):
        fid = rot[(ptr + i) % len(rot)]
        out.append(fid)
        got += float(net_kw[fid])
        if got >= need_kw:
            break
    return out


# ------------------------------------------------------------------ full plan
def plan(d: dict, req: Request, fl: Failures) -> dict:
    t0 = time.perf_counter()
    h, f, sites = d["homes"], d["feeders"], d["sites"]
    fid_of_home = h["feeder"].to_numpy()
    area_nb = req.area or list(f["neighborhood"].unique())
    area_feeders = f[f["neighborhood"].isin(area_nb)]["feeder"].tolist()
    target_kw = (req.target_mw + fl.extra_target_mw) * 1000
    steps = max(1, int(round(req.duration_h * 60 / C.INTERVAL_MIN)))
    site_kw = sites.set_index("feeder")["tier2_kw"].astype(float)
    site_in_area = sites["neighborhood"].isin(area_nb).to_numpy()
    homes_by_f = f.set_index("feeder")["homes"]
    intervals, ptr_o, ptr_f, last_o, last_f, prev_hr = [], 0, 0, [], [], 0
    trace0 = None
    for k in range(steps):
        el = k * C.INTERVAL_MIN / 60
        t = req.start_hour + el
        if int(el) != prev_hr:
            ptr_o += len(last_o)
            ptr_f += len(last_f)
            prev_hr = int(el)
        st = device_state(h, t, k, fl)
        kw, _, info = candidates(h, st, t, el, req, fl)
        fload = pd.Series(st["load"]).groupby(fid_of_home).sum()
        fload = fload.add(site_kw * 3, fill_value=0)          # site total load ~3x its flexible part
        shares = target_kw * fload[area_feeders] / fload[area_feeders].sum()

        rows = []
        fair = 1 + h["events_this_season"].to_numpy() / 10
        mode_mult = h["mode"].map(MODE_MULT).to_numpy()
        for a, arr in kw.items():
            idx = np.nonzero(arr > 0)[0]
            if not len(idx):
                continue
            cost = WEIGHT[a] * fair[idx] / CONF[a] * (mode_mult[idx] if a == "thermostat" else 1)
            rows.append(pd.DataFrame({"home_idx": idx, "feeder": fid_of_home[idx], "action": a,
                                      "kw": arr[idx], "cost": cost}))
        if LATENCY_MIN["tier2_site"] <= req.deadline_min:
            s_ok = sites[site_in_area]
            rows.append(pd.DataFrame({"home_idx": -1 - s_ok.index.to_numpy(), "feeder": s_ok["feeder"].to_numpy(),
                                      "action": "tier2_site", "kw": s_ok["tier2_kw"].to_numpy() * CONF["tier2_site"],
                                      "cost": WEIGHT["tier2_site"] / CONF["tier2_site"]}))
        cand = pd.concat(rows) if rows else pd.DataFrame(columns=["home_idx", "feeder", "action", "kw", "cost"])
        taken = allocate(cand, shares)
        planned = float(taken["kw"].sum())
        by_f = taken.groupby("feeder")["kw"].sum().reindex(f["feeder"]).fillna(0)
        by_a = taken.groupby("action")["kw"].sum().reindex(ACTIONS).fillna(0)
        short = max(0.0, target_kw - planned)
        if req.kind == "emergency":
            net = (fload - by_f).clip(lower=0)
            fo = rotation_open(f, area_feeders, net, short, ptr_f)
            oo = rotation_open(f, area_feeders, fload, target_kw, ptr_o)
        else:
            fo, oo = [], []
        last_o, last_f = oo, fo
        intervals.append({
            "k": k, "clock": t, "elapsed": el, "planned_kw": planned, "short_kw": short, "target_kw": target_kw,
            "by_action": by_a.to_dict(), "by_feeder": by_f.to_dict(),
            "count_by_action": {a: int(c) for a, c in taken.groupby("action").size().items()}, "feeder_load": fload.to_dict(),
            "shares": shares.to_dict(), "flex_open": fo, "old_open": oo,
            "dark_flex": int(homes_by_f[fo].sum()) if fo else 0,
            "dark_old": int(homes_by_f[oo].sum()) if oo else 0,
            "dark_load_flex": float(net[fo].sum()) if fo else 0.0,
            "dark_load_old": float(fload[oo].sum()) if oo else 0.0,
            "taken": taken[["home_idx", "action", "kw"]],
            "participants": int(taken.loc[taken["home_idx"] >= 0, "home_idx"].nunique()),
            "n_enrolled": int((h["enrolled"].to_numpy() & info["in_area"]).sum()),
            "n_silent_enrolled": int((h["enrolled"].to_numpy() & info["in_area"] & info["stale"]).sum()),
            "ready_by_deadline_kw": float(taken["kw"].sum()),
        })
        if k == 0:
            trace0 = _trace_first(h, st, kw, info, cand, taken, shares, req, fl, target_kw, area_feeders, fo, oo,
                                  homes_by_f)
    dt = C.INTERVAL_MIN / 60
    summ = {
        "target_mw": target_kw / 1000,
        "first_mw": intervals[0]["planned_kw"] / 1000,
        "min_mw": min(i["planned_kw"] for i in intervals) / 1000,
        "max_short_mw": max(i["short_kw"] for i in intervals) / 1000,
        "mwh": sum(min(i["planned_kw"], i["target_kw"]) for i in intervals) * dt / 1000,
        "peak_dark_flex": max(i["dark_flex"] for i in intervals),
        "peak_dark_old": max(i["dark_old"] for i in intervals),
        "home_hours_flex": sum(i["dark_flex"] for i in intervals) * dt,
        "home_hours_old": sum(i["dark_old"] for i in intervals) * dt,
        "unserved_mwh_avoided": sum(i["dark_load_old"] - i["dark_load_flex"] for i in intervals) * dt / 1000,
        "participants": int(pd.concat([i["taken"] for i in intervals]).query("home_idx >= 0")["home_idx"].nunique()),
        "kind": req.kind,
    }
    summ["voll_avoided_usd"] = summ["unserved_mwh_avoided"] * VOLL_RES
    # The deal (reporting only; allocation above is untouched). Annual contract on VERIFIED kW =
    # the lowest relief FlexGrid holds through the event. Rates and sources in config.ECONOMICS.
    E = C.ECONOMICS
    vkw = min(summ["min_mw"], summ["target_mw"]) * 1000
    summ["verified_kw"] = vkw
    summ["contract_usd_yr"] = vkw * E["contract_per_kw_yr"]
    summ["homeowner_pool_usd_yr"] = summ["contract_usd_yr"] * E["homeowner_share"]
    summ["flexgrid_usd_yr"] = summ["contract_usd_yr"] - summ["homeowner_pool_usd_yr"]
    summ["utility_alt_usd_yr"] = vkw * (E["peaker_net_per_kw_yr"] + E["td_deferral_per_kw_yr"])
    summ["utility_savings_usd_yr"] = summ["utility_alt_usd_yr"] - summ["contract_usd_yr"]
    summ["per_home_usd_yr"] = summ["homeowner_pool_usd_yr"] / max(1, summ["participants"])
    trace = trace0 + _trace_tail(intervals, summ, req) + [
        {"step": "Done", "detail": f"Planned {steps} intervals in {1000 * (time.perf_counter() - t0):.0f} ms."}]
    return {"request": asdict(req), "failures": fl.active(), "summary": summ, "intervals": intervals, "trace": trace}


def _trace_first(h, st, kw, info, cand, taken, shares, req, fl, target_kw, area_feeders, fo, oo, homes_by_f):
    area = h[info["in_area"]]
    enr = area["enrolled"].sum()
    T = []
    T.append({"step": "Request", "detail": f"{req.label}: {target_kw / 1000:.1f} MW from "
              f"{req.start_hour:g}:00 for {req.duration_h:g} h, respond within {req.deadline_min} min, "
              f"area = {', '.join(req.area) if req.area else 'whole district'} ({len(area_feeders)} feeders). "
              f"Mode: {'emergency (feeder fallback allowed)' if req.kind == 'emergency' else 'routine (never opens breakers)'}."})
    if fl.active():
        T.append({"step": "Active failures", "detail": "; ".join(fl.active())})
    T.append({"step": "Eligibility", "detail": f"{len(area):,} homes in area → {enr:,} enrolled → "
              f"{int((~info['stale'][info['in_area']] & area['enrolled'].to_numpy()).sum()):,} reporting "
              f"({int((info['stale'] & info['in_area']).sum()):,} silent > {STALE_S}s or in a comms outage are not counted)."})
    ev_ch = int((st["ev_charging"] & info["in_area"]).sum())
    T.append({"step": "Device scan @ start", "detail":
              f"AC running in {int((st['ac_on'] & info['in_area']).sum()):,} homes; {ev_ch:,} EVs charging "
              f"({int((info['ev_blocked'] & info['in_area']).sum()):,} enrolled EVs blocked: pause would miss departure charge); "
              f"{int((st['wh_on'] & info['in_area']).sum()):,} water heaters heating; "
              f"{int((st['pool_on'] & info['in_area']).sum()):,} pool pumps running."})
    avail = {a: float(v.sum()) for a, v in kw.items()}
    slow = [LABEL[a] for a in ACTIONS if LATENCY_MIN[a] > req.deadline_min]
    site_kw = float(cand.loc[cand["action"] == "tier2_site", "kw"].sum())
    if site_kw:
        avail["tier2_site"] = site_kw
    T.append({"step": "Candidates", "detail": f"{len(cand):,} possible actions, {sum(avail.values()) / 1000:.1f} MW gross: "
              + ", ".join(f"{LABEL[a]} {v / 1000:.2f} MW" for a, v in avail.items() if v > 0)
              + (f". Excluded as too slow for deadline: {', '.join(slow)}." if slow else ".")})
    T.append({"step": "Feeder shares", "detail": f"Target split across {len(shares)} feeders by load "
              f"(incl. critical feeders): {shares.min():.0f}-{shares.max():.0f} kW each."})
    got = taken.groupby("feeder")["kw"].sum().reindex(shares.index).fillna(0)
    covered = int((got >= shares - 1).sum())
    T.append({"step": "Allocation", "detail": f"Least-disruptive first (EV → pool → water heater → battery → "
              f"site Tier-2 → thermostat), fairness cap {FAIR_CAP_KW} kW/home. {covered}/{len(shares)} feeders "
              f"paid their own share; leftovers covered the rest. {len(taken):,} actions, "
              f"{taken['home_idx'].ge(0).sum():,} homes, {taken['kw'].sum() / 1000:.2f} MW."})
    short = max(0, target_kw - taken["kw"].sum())
    if short > 0:
        T.append({"step": "Shortfall", "detail": f"{short / 1000:.2f} MW cannot be covered by verified flexibility. "
                  + (f"Fallback from rotation list: open {', '.join(fo)} ({int(homes_by_f[fo].sum()):,} homes). Operator decides."
                     if fo else "Routine event: reported, no breakers opened.")})
    if oo:
        T.append({"step": "Old way (counterfactual)", "detail": f"Rotation list alone opens {', '.join(oo)} "
                  f"({int(homes_by_f[oo].sum()):,} homes dark)."})
    T.append({"step": "Safety checks", "detail": "No medical-home HVAC used · no battery below homeowner reserve · "
              "no EV below departure charge · only enrolled + reporting devices · critical-site Tier-0 loads untouched."})
    return T


def _trace_tail(intervals, summ, req):
    T = []
    prev = None
    for iv in intervals:
        if prev is not None and abs(iv["planned_kw"] - prev) > 300:
            T.append({"step": f"Re-plan @ {fmt_clock(iv['clock'])}",
                      "detail": f"Relief {prev / 1000:.1f} → {iv['planned_kw'] / 1000:.1f} MW "
                                f"(thermostat decay / water-heater & pool limits / overrides)."
                                + (f" Short {iv['short_kw'] / 1000:.1f} MW." if iv["short_kw"] > 0 else "")})
        prev = iv["planned_kw"]
    return T


def fmt_clock(x: float) -> str:
    hh, mm = int(x), int(round((x - int(x)) * 60))
    return f"{(hh - 1) % 12 + 1}:{mm:02d} {'AM' if hh < 12 or hh >= 24 else 'PM'}"


def explain_home(d: dict, home_id: str, req: Request, fl: Failures, k: int, taken: pd.DataFrame) -> dict:
    h = d["homes"]
    i = int(np.nonzero(h["home_id"].to_numpy() == home_id)[0][0])
    one = h.iloc[[i]].reset_index(drop=True)
    el = k * C.INTERVAL_MIN / 60
    t = req.start_hour + el
    st = device_state(one, t, k, fl)
    kw, why, _ = candidates(one, st, t, el, req, fl, explain=True)
    mine = taken[taken["home_idx"] == i]
    acts = []
    for a in [x for x in ACTIONS if x != "tier2_site"]:
        sel = mine[mine["action"] == a]
        acts.append({"action": LABEL[a],
                     "status": "DISPATCHED" if len(sel) else ("available, not needed" if kw[a][0] > 0 else "not available"),
                     "kw": round(float(sel["kw"].sum()) if len(sel) else float(kw[a][0]), 2),
                     "reason": "" if len(sel) else (why[a][0] or "Lower-disruption actions covered the target")})
    rec = {k_: (v.item() if hasattr(v, "item") else v) for k_, v in h.iloc[i].to_dict().items() if not k_.startswith("u_")}
    rec.update({"load_now_kw": round(float(st["load"][0]), 2), "ac_on": bool(st["ac_on"][0]),
                "ev_charging": bool(st["ev_charging"][0]), "actions": acts,
                "relief_kw": round(float(mine["kw"].sum()), 2)})
    return rec
