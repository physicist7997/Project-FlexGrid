# FlexLadder — shed watts, not homes

A reliability-aware flexibility dispatcher. An operator (utility / co-op / VPP / battery-fleet desk) enters a
realistic request — MW or kW, start, duration, response deadline, area — and FlexLadder builds an exact,
home-by-home dispatch plan from 50,000 synthetic homes, each with its own devices and the limits its homeowner
set in the FlexLadder homeowner app. Every feeder pays its share in small slices of flexibility first; any
honest shortfall goes to the utility's normal feeder rotation list, and the operator decides.

**Simulation only. No live grid, device, or battery controls. All homes and addresses are synthetic.**

## Run

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn server:app --port 8000
# Simple demo (for judges):  http://localhost:8000/simple
# Operator console:  http://localhost:8000
# Homeowner app:     http://localhost:8000/homeowner
pytest -q                                              # 10 safety/honesty tests
```
Leaflet and Chart.js are bundled in `static/vendor/`, so the app works without internet; only the
background map tiles need a connection (the map still works without them).

## Simple demo (`/simple`) — start here with judges
1. **Build a neighborhood**: sliders for number of homes (1k–50k), % signed up, % with batteries / EVs / smart thermostats → Generate.
2. **The crisis**: 104°F evening, substation overload, "cut X MW within 30 min for N hours" (defaults to ~6% of the neighborhood's load).
3. **⚡ REQUEST LOAD-SHED BACKUP** → minute-by-minute walkthrough: batteries → EV chargers paused → pool pumps → water heaters →
   thermostats → critical sites, each with counts and MW, a progress bar, and an honest shortfall line.
4. **Old way vs FlexLadder**: homes completely dark, side by side, with a grid where each square is a block of homes.
5. **Next hours + curveballs**: relief per hour vs need; inject a cell outage / battery-company outage / skip wave / extra heat and watch it re-plan.

Demo tip: run once at 20% signed up (partial coverage, fewer homes dark), then slide to 40–50% and run again (0 homes dark).

## What's in the console

| Area | What it does |
|---|---|
| **Map** | District → click a neighborhood → its 4 feeder subsectors → click a feeder → every house as a dot (dispatched / enrolled / not reporting / not enrolled / medical / breaker open). Click a house for its full record: devices, battery size and charge, EV, thermostat, the homeowner's settings, and *why* each action was or wasn't used. 🎲 buttons pick a random (dispatched) house. Critical sites shown with icons. |
| **FlexLadder / Old way toggle** | Same request, rotation list only: which feeders go fully dark. |
| **Time slider + chart** | Relief by action over the event vs the target; thermostat relief decays, water heaters and pools hit their limits, batteries are energy-limited. |
| **Dispatch tab** | Templates: Normal Tuesday (LA peak event), Crisis heat-storm overload, 10-minute emergency call, local North Hollywood overload, Texas 4CP. Or a custom request in MW or kW → **Find resources**. Per-action breakdown + CSV export of the exact dispatch list. |
| **Database tab** | Search homes by ID/address, filter (enrolled, dispatched, silent, battery, EV, thermostat, pool, medical); feeder table with load, share, flexibility, status. Click any row to fly there. |
| **Failures tab** | Inject: comms outage in a neighborhood, thermostat-brand cloud outage, partner battery API down, override wave, telemetry dropouts, hotter than forecast — or random. Plan recalculates instantly. Regenerate a new dataset (size, enrollment, seed). |
| **Trace tab** | Step-by-step planner trace: request → eligibility → device scan → candidates → feeder shares → allocation → shortfall → old-way counterfactual → safety checks → re-plans, plus injected failures and operator decisions. Deterministic — no LLM in the control loop. |
| **Banner decisions** | When short: approve feeder fallback / ask battery partner to lower reserve / send voluntary conservation text — logged to the trace. |

## Homeowner app (`/homeowner`)
Phone-style page for one home: Flex Rewards opt-in, per-device toggles and the one question that matters
(EV ready-by time and minimum charge, comfort ceiling, battery backup reserve), preference mode, never-touch list,
language and alert channel, tonight's SMS preview (English/Spanish) and an illustrative earnings estimate.
**Save** updates the shared dataset and recomputes the operator's plan; the house popup shows the new settings.

## Planner rules (planner.py)
1. Only enrolled, reporting (telemetry ≤ 180 s), in-area devices that are actually running count.
2. Response deadline: slower resources are excluded (battery 1 min, EV 2, thermostat/water heater/pool 5, site BMS 10).
3. Hard limits: EV keeps the homeowner's departure charge; battery never below the homeowner's reserve and only the
   share not already committed to the utility's own contract; comfort ceiling; water heater ≤ 60 min; pool ≤ 2 h;
   medical homes' HVAC never touched; never-touch lists honored; fairness cap 4.5 kW per home.
4. Split the target across all feeders in the area by load (including critical feeders), least disruptive first
   (EV → pool → water heater → battery → critical-site Tier-2 → thermostat), then leftovers anywhere; whole devices;
   overshoot trimmed.
5. Emergency only: shortfall → utility's rotation list (critical and relay-reserved feeders never opened).
   Routine requests never open breakers.

## Files
`config.py` assumptions + templates · `dataset.py` synthetic district and homeowner settings · `planner.py` engine ·
`server.py` API · `static/` console, homeowner app, vendored libraries · `tests/` safety tests.

All device penetrations, confidences and incentive rates are illustrative assumptions; sources are noted in `config.py`.
