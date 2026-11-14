import datetime as dt, json, sys
sys.argv_stage = sys.argv[1]
exec(open("seed.py").read().split("site = user(")[0])   # helpers only
ids = json.load(open("ids.json")); sid = ids["site"]
stage = sys.argv[1]
if stage == "signin":
    inst = user("POST", f"/sites/{sid}/actors/instances", {"provider": "actron_neo", "name": "Living room air conditioner"})
    actor("PATCH", f"/actor/instances/{inst['id']}/discovered-config", {"auth_state": {"user_code": "WDJB-MJHT", "verification_uri": "https://nimbus.actronair.com.au/device", "expires_at": iso(NOW + dt.timedelta(minutes=9))}})
    ids["instance"] = inst["id"]; json.dump(ids, open("ids.json", "w")); print(inst["id"])
elif stage == "ready":
    iid = ids["instance"]
    actor("PATCH", f"/actor/instances/{iid}/discovered-config", {"config": {"refresh_token": "demo"}, "auth_state": None})
    zones = [{"value": z, "label": z} for z in ["Study", "Family", "Bedrooms"]]
    modes = [{"value": m, "label": m.title()} for m in ["COOL", "HEAT", "AUTO", "FAN"]]
    actor("PUT", f"/actor/instances/{iid}/capabilities", {"version": 1,
      "actions": [
        {"id": "system_off", "label": "Turn off", "restorable": True, "params": []},
        {"id": "system_on", "label": "Turn on", "restorable": True, "params": [{"name": "mode", "label": "Mode", "type": "choice", "options": modes}]},
        {"id": "set_temperature", "label": "Set temperature", "restorable": True, "params": [{"name": "temperature_c", "label": "Temperature", "type": "number", "min": 16, "max": 30, "unit": "C"}]},
        {"id": "set_zones", "label": "Switch zones", "restorable": True, "params": [{"name": "zones", "label": "Zones", "type": "multi_choice", "options": zones}, {"name": "enabled", "label": "Turn on", "type": "bool"}]}],
      "readings": [
        {"id": "indoor_temp_c", "label": "Indoor temperature", "unit": "C", "type": "number"},
        {"id": "outdoor_temp_c", "label": "Outdoor temperature", "unit": "C", "type": "number"},
        {"id": "humidity_pct", "label": "Humidity", "unit": "%", "type": "number"},
        {"id": "zone_temp_c", "label": "Zone temperature", "unit": "C", "type": "number", "target_options": zones}]})
    actor("PUT", f"/actor/instances/{iid}/readings", {"state": {"is_on": True, "mode": "COOL", "fan_mode": "AUTO", "cool_setpoint_c": 23.0, "heat_setpoint_c": 20.0,
      "enabled_zones": {"Study": True, "Family": True, "Bedrooms": False}, "indoor_temp_c": 25.4, "outdoor_temp_c": 31.2, "humidity_pct": 48.0,
      "zone_temps_c": {"Study": 25.9, "Family": 25.1, "Bedrooms": 26.0}, "zone_setpoints_c": {"Study": 23.0, "Family": 23.0, "Bedrooms": 23.0},
      "zone_ranges_c": {"Study": [20.0, 26.0], "Family": [20.0, 26.0], "Bedrooms": [20.0, 26.0]}, "setpoint_min_c": 16.0, "setpoint_max_c": 30.0, "updated_at": iso(NOW)}})
    s1 = user("POST", f"/sites/{sid}/actors/schedules", {"instance_id": iid, "name": "Off during the weekday peak", "trigger": {"type": "window_start", "window_id": ids["window"]},
      "conditions": [{"reading": "outdoor_temp_c", "op": "lt", "value": 35.0, "target": None, "enabled": True}, {"kind": "months", "op": "in", "values": [11, 12, 1, 2, 3], "enabled": True}],
      "actions": [{"id": "system_off", "params": {}}], "restore": {"enabled": True, "when": "window_end"}, "notify_email": True})
    s2 = user("POST", f"/sites/{sid}/actors/schedules", {"instance_id": iid, "name": "Pre-cool before the peak", "trigger": {"type": "time", "time": "14:30", "days_of_week": [0, 1, 2, 3, 4]},
      "conditions": [{"reading": "outdoor_temp_c", "op": "gt", "value": 28.0, "target": None, "enabled": True}],
      "actions": [{"id": "set_temperature", "params": {"temperature_c": 22.0}}], "restore": {"enabled": True, "when": "after_minutes", "minutes": 90}})
    ids.update(s1=s1["id"], s2=s2["id"]); json.dump(ids, open("ids.json", "w")); print(s1["id"], s2["id"])
elif stage == "runs":
    iid = ids["instance"]
    def mkrun(sched, kind, when, outcome, msg=None, parent=None, snap=None, steps=None):
        r = actor("POST", f"/actor/instances/{iid}/runs", {"schedule_id": sched, "kind": kind, "trigger_at": iso(when), **({"parent_run_id": parent} if parent else {}), **({"snapshot": snap} if snap else {})})
        body = {"outcome": outcome, "message": msg, "attempts": 1}
        if steps: body["steps"] = steps
        actor("PATCH", f"/actor/instances/{iid}/runs/{r['id']}", body); return r["id"]
    base = NOW.replace(hour=5, minute=0) - dt.timedelta(days=1)   # 16:00 local
    step = lambda ok, aid, params, m=None: {"index": 0, "action_id": aid, "params": params, "outcome": "ok" if ok else "failed", "message": m, "attempts": 1}
    for d in (3, 2, 1):
        w = base - dt.timedelta(days=d - 1)
        a = mkrun(ids["s1"], "action", w, "ok", None, snap={"actions": [{"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}]}, steps=[step(True, "system_off", {})])
        mkrun(ids["s1"], "restore", w + dt.timedelta(hours=5), "ok", None, parent=a)
    mkrun(ids["s1"], "action", base + dt.timedelta(days=0, hours=0, minutes=1), "skipped", "outdoor_temp_c 24.0 is not below 35.0") if False else None
    mkrun(ids["s2"], "action", NOW.replace(hour=3, minute=30) - dt.timedelta(days=2), "skipped", "outdoor_temp_c 26.1 is not above 28.0")
    mkrun(ids["s2"], "action", NOW.replace(hour=3, minute=30) - dt.timedelta(days=1), "failed", "failed after 3 attempts: not confirmed", steps=[step(False, "set_temperature", {"temperature_c": 22.0}, "failed after 3 attempts: not confirmed")])
elif stage == "alerts":
    coll("dev-envoy", "PUT", f"/sources/{ids['envoy']}/alerts/firmware", {"message": "Firmware updated D8.2.4264 to D8.2.4331", "severity": "notice", "sticky": True}, "envoy")
    coll("dev-weather", "PUT", f"/sources/{ids['weather']}/alerts/barometer", {"message": "Not reporting barometer data", "severity": "partial"}, "weatherlink_live")
