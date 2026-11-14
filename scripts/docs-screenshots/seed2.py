from lib import *
random.seed(7)
user("PATCH", "/users/me/profile", {"display_name": "Alex Morgan"})
sid = user("POST", "/sites", {"name": "Home", "timezone": "Australia/Sydney"})["id"]
envoy = user("POST", f"/sites/{sid}/sources", {"kind": "energy", "provider": "envoy", "name": "Main Envoy", "config": {"host": "192.168.1.50", "token": "demo-token", "serial_num": "122200000000"}})
amber = user("POST", f"/sites/{sid}/sources", {"kind": "pricing", "provider": "amber", "name": "Amber", "config": {"amber_site_id": "01ABCDEFGHJKMNPQRSTVWXYZ00"}})
wx = user("POST", f"/sites/{sid}/sources", {"kind": "weather", "provider": "weatherlink_live", "name": "Back yard station", "config": {"host": "192.168.1.60"}})

DAYS = 31
start = NOW - dt.timedelta(days=DAYS)
cloud = {}
energy, weather = [], []
t = start
while t <= NOW:
    day = (t + dt.timedelta(hours=11)).date()
    c = cloud.setdefault(day, random.choice([1.0, 0.95, 0.8, 0.6, 0.35, 0.9, 1.0]))
    h = local_hour(t)
    sun = max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0.0
    prod = 5200 * sun * c
    cons = 420 + (350 if 7 <= h <= 9 else 0) + (1900 if 17 <= h <= 21 else 0) + 200 * math.sin(t.minute / 9) + (700 if 12 <= h < 13 else 0)
    net = cons - prod
    charging = prod > cons + 800 and 9 <= h < 15
    discharging = (not charging) and 17 <= h < 23
    soc = 25 + min(75, max(0, (h - 8) * 12)) if h < 15 and h >= 8 else (100 - (h - 15) * 11 if 15 <= h < 24 else 22)
    soc = max(18, min(100, soc))
    ts = iso(t)
    energy += [{"ts": ts, "metric": "production_w", "value": round(prod, 1)}, {"ts": ts, "metric": "consumption_w", "value": round(cons, 1)}, {"ts": ts, "metric": "battery_soc_pct", "value": round(soc, 1)}]
    energy.append({"ts": ts, "metric": "grid_import_w" if net >= 0 else "grid_export_w", "value": round(abs(net), 1)})
    if charging: energy.append({"ts": ts, "metric": "battery_charge_w", "value": round(min(3000, prod - cons), 1)})
    elif discharging: energy.append({"ts": ts, "metric": "battery_discharge_w", "value": round(min(1800, cons), 1)})
    if t.minute % 5 == 0:
        weather += [{"ts": ts, "metric": "temp_c", "value": round(17 + 10 * sun * c + 3 * math.sin(h / 24 * 6.28), 1)}, {"ts": ts, "metric": "humidity_pct", "value": round(65 - 20 * sun, 1)},
                    {"ts": ts, "metric": "wind_speed_kph", "value": round(12.3 + 4 * math.sin(h), 1)}, {"ts": ts, "metric": "wind_direction_deg", "value": 45.0},
                    {"ts": ts, "metric": "solar_radiation_wm2", "value": round(900 * sun * c, 1)}, {"ts": ts, "metric": "barometric_pressure_hpa", "value": 1014.2}]
    t += dt.timedelta(minutes=1)
for i in range(0, len(energy), 5000):
    coll("dev-envoy", "POST", f"/sources/{envoy['id']}/readings/energy", {"readings": energy[i:i+5000]}, "envoy")
for i in range(0, len(weather), 5000):
    coll("dev-weather", "POST", f"/sources/{wx['id']}/readings/weather", {"readings": weather[i:i+5000]}, "weatherlink_live")
print("readings", len(energy), len(weather))

iv, t = [], start.replace(minute=start.minute - start.minute % 5)
while t < NOW + dt.timedelta(hours=30):
    h = local_hour(t)
    buy = round(24 + (20 if 17 <= h < 21 else 0) - (14 if 10 <= h < 15 else 0) + 2 * math.sin(t.hour + t.minute), 1)
    fut = t > NOW
    base = {"interval_start": iso(t), "interval_end": iso(t + dt.timedelta(minutes=5))}
    b = {"channel": "buy", **base, "price": str(buy)}
    s = {"channel": "sell", **base, "price": str(-max(2.0, round(buy - 14, 1)))}
    if fut: b.update(price_low=str(round(buy - 4, 1)), price_high=str(round(buy + 6, 1)))
    iv += [b, s]; t += dt.timedelta(minutes=5)
for i in range(0, len(iv), 4000):
    coll("dev-pricing", "POST", f"/sources/{amber['id']}/prices", {"intervals": iv[i:i+4000]}, "amber")

user("POST", f"/sites/{sid}/tariffs", {"effective_from": "2026-07-01T00:00:00Z", "service_availability_c_per_day": "98.45", "monthly_fee_c": "3000"})
tar = user("GET", f"/sites/{sid}/tariffs")[0]["id"]
user("POST", f"/sites/{sid}/billing-cycle", {"effective_from": "2026-07-01T00:00:00Z", "cycle_months": 1, "anchor_day": 1})
allm, wk = list(range(1, 13)), [0, 1, 2, 3, 4]
dw = user("POST", f"/sites/{sid}/tariffs/{tar}/demand-windows", {"label": "Weekday peak", "months": allm, "days_of_week": wk, "start_time": "16:00", "end_time": "21:00", "rate_c_per_kw_per_day": "45"})
user("POST", f"/sites/{sid}/tariffs/{tar}/export-adjustments", {"label": "Evening bonus", "months": allm, "days_of_week": list(range(7)), "start_time": "17:00", "end_time": "21:00", "adjustment_c_per_kwh": "2", "daily_allowance_kwh": "0"})
user("POST", f"/sites/{sid}/tariffs/{tar}/conditional-credits", {"label": "Low evening import", "months": allm, "days_of_week": list(range(7)), "start_time": "17:00", "end_time": "21:00", "max_avg_import_w": "300", "credit_c": "50"})
user("POST", f"/sites/{sid}/tariffs/{tar}/monthly-fee-discounts", {"label": "First year", "starts_at": "2026-10-01T00:00:00Z", "ends_at": "2027-09-30T00:00:00Z", "discounted_fee_c": "2500"})

# Second site on a fixed tariff.
sid2 = user("POST", "/sites", {"name": "Holiday House", "timezone": "Australia/Brisbane"})["id"]
loc = user("POST", f"/sites/{sid2}/sources", {"kind": "pricing", "provider": "local", "name": "Origin Fixed", "config": {}})
for ch, label, rate, st, en in [("buy", "Peak", "32", "07:00", "23:00"), ("buy", "Off-peak", "22", "23:00", "07:00"), ("sell", "Feed-in", "6", "00:00", "00:00")]:
    user("POST", f"/sites/{sid2}/sources/{loc['id']}/rate-windows", {"channel": ch, "label": label, "effective_from": "2026-07-01T00:00:00Z", "months": allm, "days_of_week": list(range(7)), "start_time": st, "end_time": en, "rate_c_per_kwh": rate})
user("POST", f"/sites/{sid2}/tariffs", {"effective_from": "2026-07-01T00:00:00Z", "service_availability_c_per_day": "110.5", "monthly_fee_c": None})
ids = {"site": sid, "site2": sid2, "window": dw["id"], "envoy": envoy["id"], "weather": wx["id"], "amber": amber["id"], "tariff": tar}
json.dump(ids, open("ids.json", "w")); print(ids)
