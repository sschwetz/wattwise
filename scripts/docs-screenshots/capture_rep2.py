import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
B = "http://localhost:5173/sites/1/reporting"
setup(1120, 1170)
go(B + "?month=2026-11", 4); shot("reporting-days")
r = js("(()=>{const t=document.querySelector('table'); const b=t.getBoundingClientRect(); const rows=t.querySelectorAll('tr'); const l=rows[rows.length-1].getBoundingClientRect(); return [b.x, l.y+scrollY, b.width, l.height]})()")
clip("reporting-total-row", r[0], r[1] - 100, r[2], r[3] + 100)
go(B + "?month=2026-11&day=2026-11-12", 4); shot("reporting-hours")
go(B + "?month=2026-11&day=2026-11-12&hour=12", 4); shot("reporting-minutes")
# loading: switch month and capture quickly
go(B + "?month=2026-11", 3)
js("history.pushState({}, '', '/sites/1/reporting?month=2026-10'); window.dispatchEvent(new PopStateEvent('popstate'))")
time.sleep(0.15); shot("reporting-loading")
setup()
