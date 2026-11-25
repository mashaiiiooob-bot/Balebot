import asyncio, json, os, sys
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests
import supa_sink as S
import tg_stats

class Clock:
    def __init__(self, t=1_800_000_000.0): self.t = t
    def __call__(self): return self.t
class R:
    def __init__(self, code=200, ok=True): self.status_code = code; self._ok = ok
    def json(self):
        if self._ok is ValueError: raise ValueError("x")
        return {"ok": self._ok}
class Sess:
    def __init__(self, *rs): self.rs, self.calls = list(rs), []
    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers)); r = self.rs.pop(0) if self.rs else R()
        if isinstance(r, Exception): raise r
        return r
def mk(sess=None, **kw):
    clk = Clock(); s = S.SupaSink("https://x.supabase.co/functions/v1/balebot-ingest", "KEY" * 12, session=sess or Sess(), clock=clk, **kw); return s, clk

# ---- NullSink و from_env
os.environ.pop("SUPABASE_INGEST_URL", None); os.environ.pop("SUPABASE_INGEST_KEY", None)
n = S.from_env(); assert not n.enabled
n.event("a", 1, "sent"); n.status("r", 1, {}); n.channel_and_posts({}); assert n.flush_sync() is True and n.status_info() == {"enabled": False}
os.environ["SUPABASE_INGEST_URL"] = "https://u"; assert not S.from_env().enabled          # فقط یکی از دو مقدار → غیرفعال
os.environ["SUPABASE_INGEST_KEY"] = "k"; assert S.from_env().enabled
os.environ.pop("SUPABASE_INGEST_URL"); os.environ.pop("SUPABASE_INGEST_KEY")

# ---- رویداد: ارسال دسته‌ای با هدر کلید؛ هیچ متنی نیست
s, clk = mk(); s.dest = "bale+tg"
s.event("a1b2c3d4e5f6", 101, "sent", None, 5); s.event("a1b2c3d4e5f6", 102, "rejected", "ad"); s.event("zz", 103, "failed", "http_404")
assert s.flush_sync() is True
url, payload, hdr = s.session.calls[0]
assert url.endswith("balebot-ingest") and hdr == {"x-ingest-key": "KEY" * 12} and payload["kind"] == "events"
rows = payload["rows"]; assert [r["outcome"] for r in rows] == ["sent", "rejected", "failed"] and rows[0]["dest"] == "bale+tg" and rows[1]["dest"] is None and rows[0]["importance"] == 5
assert set(rows[0]) == {"ts", "src", "msg_id", "outcome", "reason", "importance", "dest"}        # ستون‌های مجاز؛ هیچ فیلد متنی
assert s.status_info()["queued"]["events"] == 0 and s.status_info()["rows_sent"] == 3 and s.status_info()["last_ok_utc"]
assert s.flush_sync() is True and len(s.session.calls) == 1                                       # صف خالی → تماس اضافه نیست
# ---- بیش از batch: چند تماس
s, clk = mk(batch=2)
for i in range(5): s.event("a", i, "rejected", "ad")
assert s.flush_sync() and [len(c[1]["rows"]) for c in s.session.calls] == [2, 2, 1]
# ---- status: فقط هر status_every ثانیه، و پاک‌سازی نوع‌ها
s, clk = mk(); big = {"seen": 5, "sent": 2, "last_fail": "x" * 500, "obj": object(), "nested": {"a": {"b": {"c": {"d": 1}}}}}
s.status("run1", 12, big, {"tg": {"sent": 1}}); s.status("run1", 13, big); assert len(s.q["status"]) == 1                 # throttle
clk.t += 301; s.status("run1", 14, big); assert len(s.q["status"]) == 2
r0 = s.q["status"][0]; assert r0["stats"]["last_fail"] == "x" * 120 and r0["stats"]["obj"] is None and r0["extra"] == {"tg": {"sent": 1}}
assert r0["stats"]["nested"]["a"]["b"]["c"] is None                                                                      # عمق محدود
json.dumps(s.q["status"][0])                                                                                              # قابل serialize
# ---- خطاها: هرگز استثنا نمی‌دهد و پیام گم نمی‌شود
s, clk = mk(Sess(R(500)))
s.event("a", 1, "sent"); assert s.flush_sync() is False and s.status_info()["queued"]["events"] == 1 and s.last_error == "http_500"
assert s.flush_sync() is False and len(s.session.calls) == 1                         # backoff: بلافاصله دوباره نمی‌زند
clk.t += 400; s.session.rs = [R(200)]; assert s.flush_sync() is True and s.status_info()["queued"]["events"] == 0
s, clk = mk(Sess(requests.ConnectionError("https://u/KEYKEYKEY")))
s.event("a", 1, "sent"); assert s.flush_sync() is False and s.last_error == "ConnectionError" and "KEY" not in str(s.status_info())
s, clk = mk(Sess(R(401))); s.event("a", 1, "sent"); assert not s.flush_sync() and s._pause_until == clk.t + 600 and s.status_info()["queued"]["events"] == 1
assert not s.flush_sync() and len(s.session.calls) == 1                              # ۱۰ دقیقه مکث
s, clk = mk(Sess(R(400))); s.event("bad", 1, "sent"); s.event("ok", 2, "sent"); s.flush_sync()
assert s.c["dropped_bad"] == 2 and s.status_info()["queued"]["events"] == 0          # دسته‌ی معیوب صف را نمی‌بندد
s, clk = mk(Sess(R(200, ok=False))); s.event("a", 1, "sent"); assert not s.flush_sync() and s.status_info()["queued"]["events"] == 1   # 200 ولی ok=false
s, clk = mk(Sess(R(200, ok=ValueError))); s.event("a", 1, "sent"); assert not s.flush_sync()                               # پاسخ غیر JSON
# ---- سقف حافظه
s, clk = mk(cap=3)
for i in range(10): s.event("a", i, "rejected", "ad")
assert len(s.q["events"]) == 3 and s.c["dropped_overflow"] == 7 and [r["msg_id"] for r in s.q["events"]] == [7, 8, 9]
# ---- هرگز استثنا نمی‌دهد حتی با ورودی خراب
s, clk = mk(); s.event(None, None, "sent", object()); s.status(None, "x", None); s.channel_and_posts(None); s.channel_and_posts({"posts": [{"bad": 1}]})
# ---- آمار کانال و پست‌ها از خروجی tg_stats
NOW = 1_800_000_000
posts = [{"id": i, "ts": NOW - h * 3600, "views": v, "forwards": 1, "replies": 0, "reactions": {"👍": 2}, "text": "SECRET-TEXT", "kind": "photo", "pinned": False}
         for i, (h, v) in enumerate([(1, 100), (5, 200), (24 * 9, 300)], 1)]
s, clk = mk(); s.channel_and_posts({"channel": {"title": "T", "username": "bale_dr", "subs": 411}, "posts": posts})
ch = s.q["channel"][0]; assert ch["channel"] == "bale_dr" and ch["subs"] == 411 and ch["posts"] == 2 and ch["views_24h"] == 300 and ch["reactions_24h"] == 4
pm = list(s.q["posts"]); assert [p["msg_id"] for p in pm] == [1, 2] and pm[0]["reactions"] == {"👍": 2} and all("text" not in p for p in pm)     # پست ۹روزه نیامد؛ متن نیامد
s.channel_and_posts({"channel": {"username": "bale_dr", "subs": 412}, "posts": posts}); assert len(s.q["channel"]) == 1 and len(s.q["posts"]) == 2          # throttle
clk.t += 3400; s.channel_and_posts({"channel": {"username": "bale_dr", "subs": 413}, "posts": posts}); assert len(s.q["channel"]) == 2 and len(s.q["posts"]) == 2
clk.t += 21600; s.channel_and_posts({"channel": {"username": "bale_dr", "subs": 414}, "posts": posts}); assert len(s.q["posts"]) == 4
assert s.flush_sync(); assert [c[1]["kind"] for c in s.session.calls] == ["channel", "posts"]; assert "SECRET-TEXT" not in json.dumps([c[1] for c in s.session.calls])
# ---- حلقه‌ی پس‌زمینه + purge روزانه
async def loop_test():
    s, clk = mk(flush_every=1, purge_every=1); s.event("a", 1, "sent"); clk.t += 5
    t = asyncio.create_task(s.run()); await asyncio.sleep(0.4); s.stop(); await asyncio.wait_for(t, 5); return s
s = asyncio.run(loop_test()); kinds = [c[1]["kind"] for c in s.session.calls]; assert kinds[0] == "events" and "purge" in kinds and s.status_info()["queued"]["events"] == 0
print("SUPA SINK OK")
