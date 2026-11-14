import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
setup(); go("http://localhost:5173/sites/1", 5)
capture_screenshot(OUT + "dashboard-overview.png", full=True)
r = js("(()=>{const h=document.querySelector('h1'); let col=h.parentElement; while(col.children.length<4) col=col.parentElement; const first=col.children[1].getBoundingClientRect(); const w=[...document.querySelectorAll('p,div,span')].find(x=>x.children.length==0 && x.innerText.trim()==='Weather').getBoundingClientRect(); return [first.x,first.y+scrollY,first.width,first.height,w.y+scrollY]})()")
print(r)
x,y,w,h,wy = r
clip("dashboard-live-tiles", x, y, w, wy - y - 6)
clip("dashboard-weather-tiles", x, wy - 6, w, y + h - wy + 6)
card = lambda t: f"[...document.querySelectorAll('h2')].find(h=>h.innerText.trim()==={t!r}).closest('[class*=rounded]')"
el("dashboard-today-pricing", card("Today's Pricing"))
el("dashboard-grid-cost", card("Import / Export"))
el("dashboard-battery", card("Battery"))
setup(dark=True); go("http://localhost:5173/sites/1", 4); shot("dashboard-dark", 900)
setup(390, 844); go("http://localhost:5173/sites/1", 4); capture_screenshot(OUT + "dashboard-mobile.png")
setup()
