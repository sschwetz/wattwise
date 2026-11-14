from lib import *
ids = json.load(open("ids.json"))
start = NOW - dt.timedelta(days=31)
iv, t = [], start.replace(minute=start.minute - start.minute % 5)
while t < NOW + dt.timedelta(hours=30):
    h = local_hour(t)
    buy = 24 + (20 if 17 <= h < 21 else 0) - (14 if 10 <= h < 15 else 0)
    fut = t > NOW
    base = {"interval_start": iso(t), "interval_end": iso(t + dt.timedelta(minutes=5))}
    b = {"channel": "buy", **base, "price": str(buy)}
    s = {"channel": "sell", **base, "price": str(-max(2, buy - 14))}
    if fut: b.update(price_low=str(buy - 4), price_high=str(buy + 6))
    iv += [b, s]; t += dt.timedelta(minutes=5)
for i in range(0, len(iv), 4000):
    coll("dev-pricing", "POST", f"/sources/{ids['amber']}/prices", {"intervals": iv[i:i+4000]}, "amber")
print("repriced", len(iv))
