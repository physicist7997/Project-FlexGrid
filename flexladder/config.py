"""All simulation constants in one place.

Every number here is an ILLUSTRATIVE SIMULATION INPUT unless a source is noted.
Change values here, not in the engine. Twists and new scenarios = new entries here.
"""

SEED = 42

# ---------------------------------------------------------------- district
# ERCOT rule of thumb: 1 MW of peak load ~ 250 homes  -> ~4 kW/home at peak.
# SCE: a circuit (feeder) serves ~800-2,000 customers.
N_HOMES = 50_000
NEIGHBORHOODS = [  # 4 feeders each -> 40 feeders (San Fernando Valley flavor)
    "Van Nuys", "Reseda", "North Hollywood", "Panorama City", "Sylmar",
    "Pacoima", "Canoga Park", "Northridge", "Sherman Oaks", "Encino",
]
FEEDERS_PER_NEIGHBORHOOD = 4

# Critical sites: their feeder is EXEMPT from conventional rotation,
# but homes on it (and the site's Tier-2 loads) can still contribute flexibility.
# kW = site's pre-approved Tier-2 (non-critical) flexible load, e.g. air handlers.
CRITICAL_SITES = {
    "F03": ("Valley Presbyterian-style hospital", 400),
    "F11": ("Fire station + 911 dispatch", 20),
    "F18": ("Water pumping plant", 300),
    "F26": ("Kaiser-style medical center", 500),
    "F33": ("Cooling center (rec center)", 60),
}
# Feeders reserved for automatic under-frequency relays: also not in the manual rotation list.
UFLS_FEEDERS = ["F07", "F15", "F22", "F30", "F38"]

# ---------------------------------------------------------------- household model (heat-wave evening)
BASE_KW_MEAN = 1.1           # non-HVAC load (lights, fridge, cooking, plug loads)
AC_SHARE = 0.85              # homes with central AC
AC_KW_RANGE = (3.0, 4.5)     # central AC draw when running (research: 3-5 kW)
AC_ON_PROB = {16: 0.80, 17: 0.85, 18: 0.85, 19: 0.80, 20: 0.70, 21: 0.60, 22: 0.50}

# Device penetration (share of ALL homes) -- assumptions, state them on a slide
SMART_TSTAT_SHARE_OF_AC = 0.40
EV_SHARE = 0.12
EV_KW = 7.2
EV_CHARGING_PROB = 0.04      # most CA EVs charge after 9 PM on TOU; research: 0.1-0.3 kW avg/enrolled EV
EV_DEADLINE_OK = 0.90        # share of charging EVs whose departure deadline allows a pause
WH_ELECTRIC_SHARE = 0.10     # most LA water heaters are gas
WH_KW = 4.5
WH_ACTIVE_PROB = 0.12
WH_MAX_DELAY_H = 1.0
POOL_SHARE = 0.08
POOL_KW = 1.5
POOL_ON_PROB = 0.25
POOL_MAX_PAUSE_H = 2.0
MEDICAL_SHARE = 0.02         # declared essential loads; HVAC never nudged (conservative policy)

# Thermostat relief: 0.74-1.0 kW in hour 1 (SMUD/Nest), decays to ~22% by hour 4 (Reliant/NRG Texas).
TSTAT_RELIEF_KW = 0.9
TSTAT_DECAY = [1.00, 0.55, 0.30, 0.22]   # by elapsed hour 0,1,2,3+

# Partner battery fleet (e.g., Base). Base Gen 1: 25 kWh, ~11 kW inverter.
BATTERY_SHARE = 0.025          # ~2.5% of homes in a partner battery fleet
BATTERY_KW = 11.0
BATTERY_COMMITTED_FRAC = 0.50   # already committed to the utility's own contract -> not ours to count
BATTERY_SUSTAIN_H = 2.0         # full-output hours the partner can offer; longer events get the energy spread out

# Planning confidence (illustrative, not calibrated)
CONFIDENCE = {"ev": 0.95, "pool": 0.90, "water_heater": 0.90, "battery": 0.95, "thermostat": 0.80}

# Least-disruption order (lower = used first). Dimensionless.
DISRUPTION_WEIGHT = {"ev": 1.0, "pool": 1.2, "water_heater": 1.5, "battery": 2.0, "thermostat": 3.0}
ACTIONS = sorted(DISRUPTION_WEIGHT, key=DISRUPTION_WEIGHT.get)
ACTION_LABEL = {
    "ev": "Pause EV charging",
    "pool": "Pause pool pump",
    "water_heater": "Delay water heater",
    "battery": "Partner battery (via fleet operator)",
    "thermostat": "Thermostat +2-3°F (after pre-cool)",
    "tier2_site": "Critical-site Tier-2 trim",
}

FAIRNESS_CAP_KW = 4.5           # max appliance relief taken from any one home
STALE_THRESHOLD_S = 180         # Base blog: telemetry older than 180 s treated as stale (configurable)
DEFAULT_STALE_FRAC = 0.03       # share of devices currently not reporting

INTERVAL_MIN = 15

# ---------------------------------------------------------------- realistic requests
SCENARIOS = {
    "la_peak": {
        "label": "Everyday LA: hot-weekday peak event (DSGS/ELRP-style)",
        "kind": "routine",
        "region": "Los Angeles (San Fernando Valley)",
        "target_mw": 6.0,
        "start_hour": 17,
        "duration_h": 2.0,
        "notice": "Day-ahead notice by ~3 PM; window 4-9 PM",
        "story": "Paid peak events are called on hot afternoons (PG&E's 2022 residential ELRP had 10 event days). "
                 "The same tool trims them, so it is already in place on the bad night.",
    },
    "la_heat_storm": {
        "label": "Heat storm: Valley substation overload, must drop 12 MW 6-9 PM",
        "kind": "emergency",
        "region": "Los Angeles (San Fernando Valley)",
        "target_mw": 12.0,
        "start_hour": 18,
        "duration_h": 3.0,
        "notice": "Forecast at ~2 PM; utility's fallback is opening feeder blocks",
        "story": "Like LADWP's Sept 2024 heat storm: overloaded local cables/transformers. "
                 "Local constraint -> local flexibility directly avoids local feeder cuts.",
    },
    "tx_4cp": {
        "label": "Texas co-op 4CP peak alert (where Base operates today)",
        "kind": "routine",
        "region": "Texas co-op territory",
        "target_mw": 8.0,
        "start_hour": 16,
        "duration_h": 2.0,
        "notice": "Same-day or day-ahead alert; one of four summer peaks sets transmission costs",
        "story": "Transmission costs (~$67/kW-yr ERCOT average, Brattle for PUCT) are allocated by the four summer peaks.",
    },
}
DEFAULT_SCENARIO = "la_heat_storm"
DEFAULT_ENROLLMENT = 0.20

# ---------------------------------------------------------------- dispatch templates (realistic request types)
TEMPLATES = {
    "normal_tuesday": {
        "label": "Normal Tuesday: LA hot-weekday peak event",
        "kind": "routine", "target_mw": 6.0, "start_hour": 17.0, "duration_h": 2.0, "deadline_min": 60, "area": [],
        "why": "Paid peak events are called on hot afternoons with day-ahead notice (PG&E's 2022 residential program had 10 event days). "
               "Routine events never open breakers; shortfall is just reported.",
    },
    "crisis_heat_storm": {
        "label": "Crisis: heat-storm substation overload (Valley)",
        "kind": "emergency", "target_mw": 12.0, "start_hour": 18.0, "duration_h": 3.0, "deadline_min": 30, "area": [],
        "why": "Like LADWP's Sept 2024 heat storm: overloaded local equipment. The utility's fallback is opening "
               "feeders from its rotation list; FlexGrid works first so fewer open.",
    },
    "fast_10min": {
        "label": "Fast emergency call: 10-minute response (ERS/ECRS-style)",
        "kind": "emergency", "target_mw": 5.0, "start_hour": 19.0, "duration_h": 1.0, "deadline_min": 10, "area": [],
        "why": "Emergency products require response within 10 or 30 minutes. Slow resources are excluded automatically.",
    },
    "local_transformer": {
        "label": "Local: overloaded equipment in North Hollywood only",
        "kind": "emergency", "target_mw": 0.8, "start_hour": 18.5, "duration_h": 2.0, "deadline_min": 15,
        "area": ["North Hollywood"],
        "why": "Location matters: only resources behind the constrained equipment count.",
    },
    "tx_4cp": {
        "label": "Texas co-op 4CP peak alert (Base's market today)",
        "kind": "routine", "target_mw": 8.0, "start_hour": 16.0, "duration_h": 2.0, "deadline_min": 60, "area": [],
        "why": "Texas transmission costs (~$67/kW-yr ERCOT average, Brattle for PUCT; verify before quoting) are allocated by the four summer peak intervals.",
    },
}
DEFAULT_TEMPLATE = "crisis_heat_storm"

# ---------------------------------------------------------------- the deal (business model)
# Utility pays FlexGrid per kW of VERIFIED flexibility per year (verified = the lowest 15-min relief FlexGrid
# guarantees through the event). Priced below what the utility otherwise spends on the same dependable kW.
# Sources: Brattle, "Real Reliability: The Value of Virtual Power" (2023), Technical Appendix Table 4 (2022$,
# 400 MW of resource adequacy): net cost gas peaker $41.40M/yr, utility battery $27.47M/yr, residential VPP
# $17.14M/yr; avoided T&D assumption $15 transmission + $35 distribution per kW-yr.
ECONOMICS = {
    "contract_per_kw_yr": 100,      # OUR proposed price to the utility (assumption)
    "homeowner_share": 0.60,        # share passed to homeowners / battery partners (assumption)
    "peaker_net_per_kw_yr": 104,    # Brattle 2023: $41.40M / 400 MW
    "td_deferral_per_kw_yr": 50,    # Brattle 2023 assumption: $15 T + $35 D
    "battery_alt_per_kw_yr": 69,    # Brattle 2023: $27.47M / 400 MW
    "vpp_cost_per_kw_yr": 43,       # Brattle 2023: $17.14M / 400 MW (typical residential VPP, net)
    # For comparison only (homeowner side): LADWP Power Savers 2026 = $55 card + $40-90/yr per thermostat.
}
