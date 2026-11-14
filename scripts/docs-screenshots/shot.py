import time, base64
import os
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "webapp", "src", "docs", "images")) + "/"
def setup(w=1280, h=800, dark=False):
    cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=False)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
def go(url, wait=2.5):
    goto_url(url); wait_for_load(); time.sleep(wait)
def shot(name, h=None):
    if h:
        cdp("Emulation.setDeviceMetricsOverride", width=1280, height=h, deviceScaleFactor=2, mobile=False); time.sleep(0.5)
    capture_screenshot(OUT + name + ".png")
    if h: setup()
def clip(name, x, y, w, h):
    r = cdp("Page.captureScreenshot", format="png", clip={"x": x, "y": y, "width": w, "height": h, "scale": 1}, captureBeyondViewport=True)
    open(OUT + name + ".png", "wb").write(base64.b64decode(r["data"]))
def el(name, expr, pad=0):
    """Screenshot the element returned by the JS expression, with optional padding."""
    r = js(f"(()=>{{const e=({expr}); if(!e) return null; e.scrollIntoView({{block:'start'}}); const b=e.getBoundingClientRect(); return [b.x+window.scrollX,b.y+window.scrollY,b.width,b.height]}})()")
    if not r: print("NOT FOUND", name); return
    clip(name, max(0, r[0]-pad), max(0, r[1]-pad), r[2]+2*pad, r[3]+2*pad)
def click_text(text, sel="button,a,[role=tab],[role=menuitem]"):
    return js(f"(()=>{{const e=[...document.querySelectorAll('{sel}')].find(b=>b.innerText.trim()==={text!r}); if(e){{e.click(); return true}} return false}})()")
