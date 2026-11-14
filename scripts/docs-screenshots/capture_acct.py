import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
setup(); go("http://localhost:5173/account", 3)
capture_screenshot(OUT + "account-page.png", full=True)
