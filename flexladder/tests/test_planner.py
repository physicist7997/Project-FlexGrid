import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C  # noqa: E402
from dataset import build_district  # noqa: E402
from planner import Failures, Request, candidates, device_state, plan  # noqa: E402

D = build_district()
H = D["homes"]


def req(key, **kw):
    tp = {k: v for k, v in C.TEMPLATES[key].items() if k != "why"}
    tp.update(kw)
    return Request(**tp)


def test_dataset_shape():
    assert len(H) == C.N_HOMES and len(D["feeders"]) == 40 and H["home_id"].is_unique


def test_routine_never_opens_breakers():
    p = plan(D, req("normal_tuesday", target_mw=40), Failures())
    assert all(not i["flex_open"] and not i["old_open"] for i in p["intervals"])
    assert p["summary"]["max_short_mw"] > 0          # shortfall is reported, not hidden


def test_never_opens_critical_or_relay_feeders():
    p = plan(D, req("crisis_heat_storm", target_mw=40), Failures(battery_api_down=True))
    protected = set(D["feeders"].loc[~D["feeders"]["sheddable"], "feeder"])
    assert all(not (set(i["flex_open"]) | set(i["old_open"])) & protected for i in p["intervals"])


def test_flex_never_worse_than_old_way():
    s = plan(D, req("crisis_heat_storm"), Failures())["summary"]
    assert s["peak_dark_flex"] <= s["peak_dark_old"] and s["home_hours_flex"] <= s["home_hours_old"]


def test_medical_and_never_touch_respected():
    r = req("crisis_heat_storm")
    st = device_state(H, r.start_hour, 0, Failures())
    kw, _, _ = candidates(H, st, r.start_hour, 0, r, Failures())
    assert kw["thermostat"][H["medical"].to_numpy()].sum() == 0
    assert kw["thermostat"][H["never_touch"].str.contains("Thermostat").to_numpy()].sum() == 0
    assert kw["ev"][H["never_touch"].str.contains("EV").to_numpy()].sum() == 0


def test_battery_never_below_reserve():
    r = req("crisis_heat_storm")
    st = device_state(H, r.start_hour, 0, Failures())
    kw, _, _ = candidates(H, st, r.start_hour, 0, r, Failures())
    energy = (H["battery_soc_pct"] - H["battery_reserve_pct"]).clip(lower=0) / 100 * H["battery_kwh"] * 0.9
    assert (kw["battery"] * r.duration_h <= energy.to_numpy() + 1e-6).all()


def test_deadline_excludes_slow_resources():
    p = plan(D, req("fast_10min", deadline_min=5), Failures())
    assert p["intervals"][0]["by_action"]["tier2_site"] == 0


def test_area_restriction():
    p = plan(D, req("local_transformer"), Failures())
    nh = set(D["feeders"].loc[D["feeders"]["neighborhood"] == "North Hollywood", "feeder"])
    used = {f for f, v in p["intervals"][0]["by_feeder"].items() if v > 0}
    assert used <= nh


def test_failures_reduce_relief():
    base = plan(D, req("crisis_heat_storm"), Failures())["summary"]["first_mw"]
    for fl in [Failures(battery_api_down=True), Failures(comms_out_neighborhoods=["Van Nuys"]),
               Failures(thermostat_cloud_down=["ecobee"])]:
        assert plan(D, req("crisis_heat_storm"), fl)["summary"]["first_mw"] <= base + 1e-6


def test_more_enrollment_more_relief():
    lo = plan(build_district(enrollment=0.1), req("crisis_heat_storm"), Failures())["summary"]["first_mw"]
    hi = plan(build_district(enrollment=0.4), req("crisis_heat_storm"), Failures())["summary"]["first_mw"]
    assert hi > lo


def test_deal_adds_up_and_utility_saves():
    s = plan(D, req("crisis_heat_storm"), Failures())["summary"]
    assert s["verified_kw"] > 0
    assert abs(s["homeowner_pool_usd_yr"] + s["flexgrid_usd_yr"] - s["contract_usd_yr"]) < 1e-6
    assert s["utility_savings_usd_yr"] >= 0 and s["per_home_usd_yr"] > 0
