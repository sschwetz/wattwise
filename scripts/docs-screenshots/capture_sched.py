import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
setup()
go("http://localhost:5173/settings/sites/1/schedule", 4); shot("schedule-list", 800)
def scrolled(name, y, h):
    cdp("Emulation.setDeviceMetricsOverride", width=1280, height=h, deviceScaleFactor=2, mobile=False); time.sleep(0.4)
    js(f"window.scrollTo(0,{y})"); time.sleep(0.6)
    capture_screenshot(OUT + name + ".png"); js("window.scrollTo(0,0)"); setup()
# The second schedule has a time-of-day row, a minimum gap and a run-again-after time, so steps 3 to 7 show them.
js("[...document.querySelectorAll('button')].find(b=>b.innerText.includes('Pre-cool')).click()"); time.sleep(1.5)
scrolled("schedule-editor", 610, 1600)
# Run history sits in the schedule card under Test now; the unit state is the card after it.
r = js("(()=>{const t=[...document.querySelectorAll('button')].find(b=>b.innerText.trim()==='Test now'); const card=t.closest('section').getBoundingClientRect(); const last=[...document.querySelectorAll('h2')].find(h=>h.innerText.trim()==='Air conditioner').closest('[class*=rounded]').getBoundingClientRect(); const top=t.getBoundingClientRect().top+scrollY-12; return [card.left-4, top, card.width+8, last.bottom+scrollY+4-top]})()")
clip("schedule-run-history", r[0], r[1], r[2], r[3])
go("http://localhost:5173/sites/1", 4); shot("dashboard-needs-attention", 640)
go("http://localhost:5173/settings/sites/1/general", 3); shot("settings-general", 1040)
go("http://localhost:5173/settings/sites/1/sharing", 3); shot("settings-sharing", 560)
go("http://localhost:5173/account", 3)
js("[...document.querySelectorAll('button')].find(b=>b.innerText.includes('Alex Morgan')).click()"); time.sleep(0.8)
clip("header-menu", 1000, 8, 262, 242)
click_text("About"); time.sleep(2.5); shot("about-containers", 760)
click_text("Licences"); time.sleep(1.5); shot("about-licences", 760)
