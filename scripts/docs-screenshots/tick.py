from lib import *
ids = json.load(open("ids.json"))
now = dt.datetime.now(UTC).replace(second=0, microsecond=0)
en, wx = [], []
for k in range(12, -1, -1):
    t = now - dt.timedelta(minutes=k); h = local_hour(t)
    sun = max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0.0
    prod = 4800 * sun; cons = 520 + 180 * math.sin(t.minute / 7); net = cons - prod; ts = iso(t)
    en += [{"ts": ts, "metric": "production_w", "value": round(prod, 1)}, {"ts": ts, "metric": "consumption_w", "value": round(cons, 1)}, {"ts": ts, "metric": "battery_soc_pct", "value": 93.6},
           {"ts": ts, "metric": "grid_import_w" if net >= 0 else "grid_export_w", "value": round(abs(net), 1)}, {"ts": ts, "metric": "battery_charge_w", "value": 1200.0}]
    wx += [{"ts": ts, "metric": "temp_c", "value": 27.4}, {"ts": ts, "metric": "humidity_pct", "value": 46.0}, {"ts": ts, "metric": "wind_speed_kph", "value": 12.3},
           {"ts": ts, "metric": "wind_direction_deg", "value": 45.0}, {"ts": ts, "metric": "solar_radiation_wm2", "value": round(900 * sun, 1)}, {"ts": ts, "metric": "barometric_pressure_hpa", "value": 1014.2}, {"ts": ts, "metric": "rain_mm", "value": 0.0}]
coll("dev-envoy", "POST", f"/sources/{ids['envoy']}/readings/energy", {"readings": en}, "envoy")
coll("dev-weather", "POST", f"/sources/{ids['weather']}/readings/weather", {"readings": wx}, "weatherlink_live")
print("tick ok")
