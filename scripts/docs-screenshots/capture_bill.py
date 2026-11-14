import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
H = lambda t: f"[...document.querySelectorAll('h2')].find(h=>h.innerText.trim()==={t!r}).closest('[class*=rounded]')"
go("http://localhost:5173/settings/sites/1/tariffs", 3)
card = js(f"(()=>{{const b={H('Amber Tariffs')}.getBoundingClientRect(); return [b.x,b.y+scrollY,b.width,b.height]}})()")
print("card", card)
clip("billing-tariffs", card[0], card[1], card[2], 520)
forms = js("(()=>{const f=[...document.querySelectorAll('form')]; const a=f[0].getBoundingClientRect(), z=f[3].getBoundingClientRect(); return [a.x,a.y+scrollY,a.width,z.bottom-a.top]})()")
clip("billing-add-window-forms", forms[0]-8, forms[1]-8, forms[2]+16, forms[3]+16)
f=js("(()=>{const b=[...document.querySelectorAll('form')][4].getBoundingClientRect(); return [b.x,b.y+scrollY,b.width,b.height]})()")
clip("billing-add-tariff", f[0]-8, f[1]-8, f[2]+16, f[3]+16)
go("http://localhost:5173/settings/sites/1/billing", 3)
el("billing-cycle", H("Billing cycle"), 0)
el("billing-demand-charges", H("Demand charges"), 0)
