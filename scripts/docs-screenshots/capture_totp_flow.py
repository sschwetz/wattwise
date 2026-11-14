import os
import subprocess, json
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
card = "document.querySelector('form, [data-card], div.rounded-xl') && (document.querySelector('div.rounded-xl'))"
def fill(sel, val):
    js(f"(()=>{{const e=document.querySelector({sel!r}); const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(e,{val!r}); e.dispatchEvent(new Event('input',{{bubbles:true}}))}})()")
js("localStorage.clear()")
go("http://localhost:5173/login", 1.5)
fill("input[type=email]", "taylor@example.com"); fill("input[type=password]", "demo-password-1")
click_text("Log in"); time.sleep(3)
print(page_info()["url"])
time.sleep(1.5)
el("totp-setup", "document.querySelector('div.rounded-xl')", 12)
txt = js("document.body.innerText")
import re
key = re.search(r"Manual entry key[\s\S]*?([A-Z2-7]{16,})", txt)
print("key", bool(key))
open(OUTKEY, "w").write(key.group(1) if key else "")
