# Documentation screenshots

These scripts rebuild the demo site behind the screenshots in `webapp/src/docs/images/`, so every image shows the same data (site "Home", user "Alex Morgan", an Envoy, Amber and a weather station, a tariff with demand, export, credit and fee rules, an air conditioner with two schedules, and a second site on a fixed tariff).

Retake all the images in one pass. Mixing images from different demo runs shows different dates, commits and numbers.

## Steps

1. Start core-api on port 8000 with a fresh database and these keys: `ENVOY_COLLECTOR_KEY=dev-envoy`, `WEATHER_COLLECTOR_KEY=dev-weather`, `PRICING_COLLECTOR_KEY=dev-pricing`, `ACTOR_KEY=dev-actor`. Set `GIT_COMMIT` to the current commit so the header and About dialog show it. Start the webapp with `npm run dev` on port 5173.
2. From `core-api/`, run each script with `PYTHONPATH=. uv run python ../scripts/docs-screenshots/<script>.py` (the seed scripts import `lib.py` from their own folder, so add that folder to `PYTHONPATH` too): `seed2.py`, `jobs.py` (computes demand charges), then `stage.py signin`.
3. Start a separate Chrome with `--remote-debugging-port=9333` and an empty `--user-data-dir`. Run the `capture_*.py` scripts through `browser-use` with `BU_CDP_URL=http://localhost:9333`. Take `capture_si.py` (the sign-in code) before `stage.py ready`, `runs` and `alerts`.
4. Run `tick.py` just before the dashboard captures. The live tiles only show readings from the last few minutes.
5. Post a commit report for each service to `/api/v1/services/report` and set the `webapp` row in `service_status`, or the About dialog shows "not reporting".

The scripts use light theme at 1280 px wide and 2x scale, to match the existing images. The seed data is generated, and the secrets in it are placeholders.
