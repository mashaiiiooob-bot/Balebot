import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import poll
from state import State
from tg_panel import Panel
from types import SimpleNamespace as NS

class Ent: id = 42; title = "T"; username = "t"
st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
# توقف ارسال: process_source اصلاً به کلاینت/بله دست نمی‌زند و چیزی تغییر نمی‌کند
st.data["paused"] = True
class Boom:
    def __getattr__(self, n): raise AssertionError("paused: client must not be touched (" + n + ")")
asyncio.run(poll.process_source(Boom(), Boom(), st, Ent(), tempfile.mkdtemp()))
assert st.data["checkpoints"] == {} and not st.dirty
# project_snapshot: فقط‌خواندنی و با ساختار مورد نیاز پنل
before_base, before_run = dict(poll.STATS_BASE), dict(poll.STATS)
poll.STATS["seen"] = poll.STATS.get("seen", 0) + 7
snap = poll.project_snapshot(st, 32, 1, None)
assert snap["totals"]["seen"] == before_base.get("seen", 0) + poll.STATS["seen"] and snap["sources"] == {"count": 32, "failed": 1}
assert snap["paused"] is True and snap["tg_lane"] is None and snap["uptime_min"] >= 0 and isinstance(snap["ai_providers"], list)
assert poll.STATS_BASE == before_base and poll.STATS["seen"] == before_run.get("seen", 0) + 7           # چیزی تغییر نکرد
poll.STATS["seen"] -= 7
# رندر پنل با خروجی واقعی project_snapshot
async def coll(c, ch, l, a): return {"channel": {"title": "x", "username": "u", "subs": 5}, "posts": [], "admin": None}
pn = Panel(NS(), [1], st, None, "@c", lambda: poll.project_snapshot(st, 3, 0, None), collector=coll)
for key in ("pj", "hl", "ctl"):
    t, kb = asyncio.run(pn.page(key)); assert t and kb
s = pn.status(); assert s["channel_check"] is None and s["admin_stats"] is None
asyncio.run(pn.page("m")); s = pn.status(); assert s["channel_check"]["posts"] == 0 and s["channel_check"]["subs"] == 5
print("PANEL HOOKS OK")
