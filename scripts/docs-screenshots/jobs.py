import datetime as dt
from core_api.db import make_engine, make_session_factory
from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.jobs.demand_charge_job import run_demand_charge_job
sf = make_session_factory(make_engine())
now = dt.datetime.now(dt.timezone.utc)
t = now - dt.timedelta(days=31)
while t < now:
    t += dt.timedelta(hours=3)
    db = sf(); run_demand_interval_job(db, min(t, now)); db.close()
db = sf(); run_demand_charge_job(db, now); db.close()
from sqlalchemy import text
db = sf(); print(db.execute(text("select count(*) from demand_intervals")).scalar(), db.execute(text("select period_start, peak_kw, charge_c, is_estimated from demand_charges")).fetchall()[:3] if False else db.execute(text("select * from demand_charges")).fetchall()[:3])
