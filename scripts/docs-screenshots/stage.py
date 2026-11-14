import sys
exec(open("actor_seed.py").read().replace('exec(open("seed.py").read().split("site = user(")[0])   # helpers only', 'from lib import *'))
