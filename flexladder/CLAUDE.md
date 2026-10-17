# FlexLadder — project context for Claude Code

## What this is
Hackathon project for the InnovateLA Dual-Use Hackathon (USC, Oct 16–18 2026), Energy & Grid track sponsored by
Base Power (Texas home-battery company). Team of 4. **Devpost submission due Sat Oct 17, 10:00 PM PT, no extensions;
target 9:00 PM.** Finals Sunday 1:15 PM (5-min pitch + 5-min Q&A). Judging (1–5 each): dual-use fit (tiebreaker),
technical execution, feasibility & impact, adapting to twists, demo/pitch. Base criteria: working model not a concept,
deployable by utility/city/homeowner, homes or outage-hours protected, saves money daily and keeps power on in outages.

**Idea: "shed watts, not homes."** When a utility must cut X MW fast, today it opens whole feeder breakers
(~800–2,000 homes each) from a rotation list. FlexLadder spreads the cut thinly across many opted-in homes and sites:
partner batteries, EV charging pauses, pool pumps, water-heater delays, thermostat nudges, critical-site Tier-2 loads —
least disruptive first, within each homeowner's limits — and honestly reports any shortfall, which goes to the
utility's normal rotation list for a human to approve. Same engine runs ordinary peak events (dual use).

## Run
```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
# /simple     simple judge demo (start here)
# /           full operator console (map drill-down, database, failures, trace)
# /homeowner  homeowner app (edits flow into the operator plan)
pytest -q     # 10 tests must stay green
```
Leaflet + Chart.js are vendored in `static/vendor/` (offline-safe). Only the OpenStreetMap basemap tiles need internet (CARTO now requires an API key).

## Files
- `config.py` — all assumptions (with source notes) + `TEMPLATES` (realistic request types) + `DEFAULT_TEMPLATE`.
  Legacy `SCENARIOS` dict and a few constants are unused leftovers; harmless.
- `dataset.py` — seeded synthetic district: 10 San Fernando Valley neighborhoods × 4 feeder subsectors = 40 feeders,
  N homes (default 50,000) with per-home devices and homeowner-app settings; 6 critical sites; 4 relay-reserved
  (UFLS) feeders. `build_district(seed, n_homes, enrollment, shares)`.
- `planner.py` — deterministic engine. Per 15-min interval: device state → eligibility/candidates (with per-home
  reasons via `explain=True`) → `allocate` (feeder pro-rata shares incl. critical feeders, least-disruptive first,
  fairness cap, whole devices, overshoot trimmed) → emergency fallback via rotation list → old-way counterfactual.
  `plan()` returns summary, intervals, trace. `explain_home()` for popups. `Failures` dataclass for injections.
- `server.py` — FastAPI, single-user in-memory state `S` (dataset, failures, last request, last plan).
  Endpoints: /api/plan (GET/POST), /api/failure, /api/failures/reset, /api/dataset, /api/geo, /api/templates,
  /api/homes?feeder=, /api/home/{id}, /api/random_home, /api/search, /api/feeders, /api/homeowner/{id} (GET/POST),
  /api/export.csv. `_clean()` makes responses JSON-safe (NaN → None, numpy → python).
- `static/simple.html` — 5-step story page (build neighborhood → crisis → big REQUEST LOAD-SHED BACKUP button →
  minute-by-minute actions with counts/MW → old way vs FlexLadder grids → hourly bars + curveballs).
- `static/index.html`, `app.js`, `style.css` — operator console. `static/homeowner.html` — phone-style homeowner app.
- `tests/test_planner.py` — safety/honesty invariants.

## Non-negotiable design rules (judges and Base engineers will probe these)
- No LLM in the control loop. The "trace" is the deterministic planner's audit log; say so.
- Never invent capacity: only enrolled, reporting (telemetry ≤ 180 s), in-area devices that are actually running.
- Hard limits: EV keeps owner's departure charge; battery never below owner's reserve and only the share NOT already
  committed to the utility's own contract (Base/utility contracts keep dispatch control); comfort ceiling; water heater
  ≤ 60 min; pool ≤ 2 h; medical homes' HVAC never touched; never-touch lists; critical-site Tier 0 untouched.
- Routine requests never open breakers. Emergency fallback never opens critical or relay-reserved feeders.
- FlexLadder works BEFORE / ALONGSIDE a mandatory shed order; it never delays one (ERCOT: each shed block within
  30 minutes). Crisis framing = local capacity emergency (e.g., LADWP Sept 2024 heat storm overloads), where local
  flexibility directly avoids local feeder cuts.
- Base is a partner/channel: FlexLadder *requests* battery capacity through the fleet operator, never commands it.
- Keep numbers realistic and labeled as assumptions; don't add unsourced statistics to UI or docs.

## Current state (Sat ~1:30 PM)
All three pages work end to end (tested in headless Chromium, no JS errors). Default crisis (50k homes, 20% enrolled,
12 MW 6–9 PM): ~9 MW from flexibility in hour 1, homes dark at peak 1,810 vs 4,053 old way; hour-3 shortfall shown
honestly. Simple demo default (20k homes, 5 MW): ~67% covered at 20% enrolled; 0 homes dark at 50% enrolled.

## Priorities for the rest of the day (in order)
1. Bug-fix and polish only what the demo path touches (`/simple` first, then console). Feature freeze ~5 PM.
2. Add twist content: Devpost text + optional `config.TEMPLATES` entries
   - Twist 1 (federal funding): DOE SBIR/STTR (reauthorized Apr 13 2026 through Sept 30 2031), DOE GRIP ($10.5B,
     utility applies with us as tech partner), DoD ESTCP installation energy resilience (14-day islanding → a
     "base microgrid" scenario where housing/offices give flex so mission loads stay on).
   - Twist 2 (global): same allocator, swap three adapters (grid signal, control path, consent channel);
     e.g., Johannesburg "Stage 4" scenario (geysers + 10 A caps), India smart meters, China 2021 rationing as the
     blunt-move example. Note: SBIR security screening → "the method travels, not the product" for China.
   - Twist 3: ask the user for the text.
3. Optional if time: replace synthetic base loads with Pecan Street sample traces; move `@app.on_event("startup")`
   to a lifespan handler; README screenshots.
4. Devpost needs: name, lens (Energy & Grid), one-liner, deck, demo video (record a backup), repo, all teammates,
   responsible-use note (2 lines: outage/occupancy data could reveal empty homes or be used to deprioritize customers
   → verdicts stay in the operator console, external sharing feeder-level only, medical flags opt-in, a human approves
   every breaker action).

## Gotchas
- Simple page and console share one server state; `/simple` regenerates the dataset (seed 42) on load.
- `allocate()` takes whole devices then trims overshoot; per-feeder shares include critical feeders by design.
- Interval dicts include a pandas `taken` DataFrame; strip it before JSON (`_light()` does).
- Keep `pytest -q` green after every change; run the server and click through `/simple` before committing.
