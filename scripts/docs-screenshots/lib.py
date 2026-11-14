import datetime as dt, json, math, random, sys, urllib.request, urllib.error
from core_api.security import create_access_token
BASE = "http://localhost:8000/api/v1"
TOKEN = create_access_token(1)
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC).replace(second=0, microsecond=0)
def call(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(BASE + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read(); return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        print("ERR", method, path, e.code, e.read()[:300]); sys.exit(1)
def user(m, p, b=None): return call(m, p, b, {"Authorization": f"Bearer {TOKEN}"})
def coll(key, m, p, b=None, prov=None):
    h = {"X-Collector-Key": key}
    if prov: h["X-Collector-Provider"] = prov
    return call(m, p, b, h)
def actor(m, p, b=None): return call(m, p, b, {"X-Actor-Key": "dev-actor", "X-Actor-Provider": "actron_neo"})
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
def local_hour(t): return ((t.hour + 11) % 24) + t.minute / 60
