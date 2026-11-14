import os
exec(open(os.path.join(os.getcwd(), "scripts/docs-screenshots/shot.py")).read())
go("http://localhost:5173/settings/sites/1/schedule", 3); shot("schedule-sign-in", 420)
