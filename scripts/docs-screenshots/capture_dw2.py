import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
setup()
go("http://localhost:5173/sites/1", 5)
r = js("(()=>{const b=document.querySelector('h1').getBoundingClientRect(); return [b.x,b.y+scrollY]})()")
clip("dashboard-demand-window", r[0]-4, r[1]-2, 440, 36)
go("http://localhost:5173/sites/1/reporting", 3)
clip("sidebar-and-header", 0, 0, 420, 330)
