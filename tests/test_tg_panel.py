import asyncio, collections, datetime, os, sys, tempfile, time
from types import SimpleNamespace as NS
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tg_stats, tg_panel
from tg_panel import Panel, PanelBot, clip, esc
from telegram_out import TgError
from state import State

NOW = 1_800_000_000.0
def mkstate(): return State(os.path.join(tempfile.mkdtemp(), "state.json"))
def run(c): return asyncio.run(c)

# ---------------- post_dict / collect (با اشیای شبیه Telethon) ----------------
class ReactionEmoji:
    def __init__(self, e): self.emoticon = e
class ReactionPaid: pass
class ReactionCustomEmoji:
    document_id = 1
class MessageMediaPhoto: pass
def msg(i, hours_ago, views, fw=0, rp=0, rx=(), text="متن", media=None, action=None, pinned=False):
    return NS(id=i, date=datetime.datetime.fromtimestamp(NOW - hours_ago * 3600, datetime.timezone.utc), views=views, forwards=fw,
              replies=NS(replies=rp) if rp else None, reactions=NS(results=[NS(reaction=r, count=c) for r, c in rx]) if rx else None,
              message=text, media=media, action=action, pinned=pinned)
p1 = tg_stats.post_dict(msg(1, 2, 120, 3, 2, [(ReactionEmoji("👍"), 5), (ReactionEmoji("🔥"), 2), (ReactionPaid(), 4), (ReactionCustomEmoji(), 1)], "یک\n\n  متن   بلند " * 20, MessageMediaPhoto()))
assert p1["views"] == 120 and p1["forwards"] == 3 and p1["replies"] == 2 and p1["kind"] == "photo"
assert p1["reactions"] == {"👍": 5, "🔥": 2, "⭐": 4, "✨": 1} and len(p1["text"]) <= 80 and "\n" not in p1["text"]
p0 = tg_stats.post_dict(msg(2, 1, None)); assert p0["views"] == 0 and p0["reactions"] == {} and p0["kind"] == "text" and p0["replies"] == 0

class FakeClient:
    def __init__(self, msgs, subs=1500, stats=None, full_fail=False):
        self.msgs, self.subs, self.stats, self.full_fail, self.calls = msgs, subs, stats, full_fail, []
    async def get_entity(self, ch): return NS(title="کانال من", username="bale_dr")
    async def __call__(self, req):
        name = type(req).__name__; self.calls.append(name)
        if name == "GetFullChannelRequest":
            if self.full_fail: raise RuntimeError("x")
            return NS(full_chat=NS(participants_count=self.subs, about="درباره", linked_chat_id=7))
        if name == "GetBroadcastStatsRequest":
            if self.stats is None: raise PermissionError("CHAT_ADMIN_REQUIRED")
            return self.stats
    def iter_messages(self, ent, limit=None):
        async def g():
            for m in self.msgs[:limit]: yield m
        return g()
msgs = [msg(1, 1, 300, 5, 1, [(ReactionEmoji("👍"), 9)], "پست یک"), msg(2, 5, 100, 0, 0, [(ReactionEmoji("🔥"), 3)], "پست دو"),
        msg(3, 30, 50, 1, 0, [], "پست سه"), msg(4, 0, 0, action=NS(kind="pin")), msg(5, 26, 70, 2, 0, [(ReactionEmoji("👍"), 1)], "پست پنج"),
        msg(6, 24 * 20, 500, 10, 4, [(ReactionEmoji("👍"), 20)], "پست قدیمی")]
d = run(tg_stats.collect(FakeClient(msgs), "@bale_dr", 500, admin=False))
assert [p["id"] for p in d["posts"]] == [1, 2, 3, 5, 6]                                  # پیام سرویس حذف شد
assert d["channel"]["subs"] == 1500 and d["channel"]["username"] == "bale_dr" and d["admin"] is None
d2 = run(tg_stats.collect(FakeClient(msgs, full_fail=True), "@bale_dr", 500, admin=False)); assert d2["channel"]["subs"] is None and d2["channel"]["full_error"] == "RuntimeError"
# آمار پیشرفته: غیرقابل‌دسترس و قابل‌دسترس
adm = run(tg_stats.collect_admin(FakeClient(msgs), NS())); assert adm == {"available": False, "reason": "PermissionError"}
st = NS(period=NS(min_date=datetime.datetime.fromtimestamp(NOW - 86400 * 7, datetime.timezone.utc), max_date=datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)),
        followers=NS(current=1500, previous=1450), views_per_post=NS(current=200, previous=180), shares_per_post=NS(current=3, previous=2),
        reactions_per_post=NS(current=5, previous=4), enabled_notifications=NS(part=300, total=1500))
adm = run(tg_stats.collect_admin(FakeClient(msgs, stats=st), NS())); assert adm["available"] and adm["followers"] == {"current": 1500, "previous": 1450} and adm["notifications_pct"] == 20.0
print("collect OK")

# ---------------- summarize ----------------
posts = d["posts"]
s1 = tg_stats.summarize(posts, NOW, 1, "bale_dr"); s7 = tg_stats.summarize(posts, NOW, 7, "bale_dr"); sall = tg_stats.summarize(posts, NOW, None, "bale_dr")
assert s1["posts"] == 2 and s1["views_total"] == 400 and s1["forwards_total"] == 5 and s1["reactions_total"] == 12
assert s7["posts"] == 4 and s7["views_total"] == 520 and s7["views_avg"] == 130 and s7["views_median"] == 85 and s7["views_max"] == 300
assert s7["views_avg_mature"] == 60                                                         # فقط پست‌های بالای ۲۴ ساعت (50 و 70)
assert s7["by_emoji"] == {"👍": 10, "🔥": 3} and s7["forwards_total"] == 8 and s7["replies_total"] == 1
assert s7["er_pct"] == round(100 * (13 + 8 + 1) / 520, 2) and sall["posts"] == 5 and sall["views_total"] == 1020
assert s7["top_views"][0]["id"] == 1 and s7["top_views"][0]["link"] == "https://t.me/bale_dr/1" and s7["top_reactions"][0]["id"] == 1
assert all(p["forwards"] > 0 for p in s7["top_forwards"]) and tg_stats.summarize([], NOW, 7)["views_avg"] == 0
# بهترین ساعت: دو پست در یک ساعت تهران لازم است
base = (1_800_000_000 // 86400) * 86400 + int(6.5 * 3600)          # ۱۰:۰۰ به وقت تهران = ۰۶:۳۰ UTC
hp = [{"id": i, "ts": base + i * 60, "views": v, "forwards": 0, "replies": 0, "reactions": {}, "text": "", "kind": "text"} for i, v in enumerate([100, 200], 1)]
assert time.gmtime(hp[0]["ts"] + tg_stats.TEHRAN).tm_hour == 10
bh = tg_stats.summarize(hp, hp[0]["ts"] + 3600, 7)["best_hours"]; assert bh == [(10, 150, 2)], bh
assert tg_stats.summarize(hp[:1], hp[0]["ts"] + 3600, 7)["best_hours"] == []             # با یک پست ساعت «برتر» اعلام نمی‌شود
print("summarize OK")

# ---------------- Snapshots ----------------
stt = mkstate(); sn = tg_stats.Snapshots(stt)
assert sn.record(1000, NOW - 8 * 86400) and not sn.record(1001, NOW - 8 * 86400 + 60) and sn.record(1100, NOW - 86400 - 5000) and sn.record(1180, NOW)
assert sn.delta(1, NOW, 1180) == 80 and sn.delta(7, NOW, 1180) == 180 and sn.delta(30, NOW, 1180) is None and not sn.record(None, NOW + 9999)
stt.save(); assert State(stt.path).data["tgstats"][0] == [int(NOW - 8 * 86400), 1000]
print("snapshots OK")

# ---------------- PanelBot ----------------
class R:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self):
        if self._b is ValueError: raise ValueError("x")
        return self._b
class S:
    def __init__(self, *rs): self.rs, self.calls = list(rs), []
    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout)); r = self.rs.pop(0)
        if isinstance(r, Exception): raise r
        return r
ok = R(200, {"ok": True, "result": [1]})
s = S(ok); b = PanelBot("TOK9", session=s); assert b.get_updates(5, 25) == [1] and s.calls[0][1]["offset"] == 5 and s.calls[0][1]["allowed_updates"] == ["message", "callback_query"] and s.calls[0][2] == 40
s = S(ok); PanelBot("TOK9", session=s).send(1, "<b>x</b>", {"inline_keyboard": []}); assert s.calls[0][1]["parse_mode"] == "HTML" and s.calls[0][1]["reply_markup"] == {"inline_keyboard": []}
for resp, st_, ra in [(R(409, {"ok": False, "description": "Conflict"}), 409, 0), (R(429, {"ok": False, "parameters": {"retry_after": 9}}), 429, 9), (R(502, ValueError), 502, 0)]:
    try: PanelBot("TOK9", session=S(resp)).get_updates(); assert False
    except TgError as e: assert (e.status, e.retry_after) == (st_, ra) and "TOK9" not in str(e) and "Conflict" not in str(e)
import requests as rq
try: PanelBot("TOK9", session=S(rq.ConnectionError("https://api.telegram.org/botTOK9/x"))).get_updates(); assert False
except TgError as e: assert "TOK9" not in str(e)
assert clip("a" * 5000).endswith("…") and len(clip("خط\n" * 3000)) <= 4001 and esc("<b>&") == "&lt;b&gt;&amp;"
print("bot OK")

# ---------------- Panel: رندر، دسترسی، دکمه‌ها ----------------
class FakeBot:
    def __init__(self): self.sent, self.edits, self.answers, self.updates, self.fail = [], [], [], [], None
    def delete_webhook(self): self.sent.append("deleteWebhook")
    def set_commands(self): self.sent.append("setMyCommands")
    def send(self, chat, text, markup=None): self.sent.append((chat, text, markup))
    def edit(self, chat, mid, text, markup=None):
        if self.fail == "edit": raise TgError("not modified", status=400)
        self.edits.append((chat, mid, text, markup))
    def answer(self, cid, text=None, alert=False): self.answers.append((cid, text, alert))
    def get_updates(self, offset=0, timeout=25):
        if self.fail == "poll": raise TgError("conflict", status=409)
        ups = [u for u in self.updates if u["update_id"] >= offset]
        self.updates = [u for u in self.updates if u["update_id"] < offset]; return ups
PROJ = {"totals": {"seen": 1000, "sent": 100, "ad": 50, "dropword": 20, "profanity": 5, "duplicate": 30, "not_news": 400, "too_old": 300, "failed": 2, "ai_calls": 10, "gate_calls": 5, "ai_ad": 3, "ai_banned": 1, "ai_rescued": 2, "ai_errors": 1},
        "run": {"seen": 60, "sent": 8, "failed": 0}, "uptime_min": 135, "sources": {"count": 32, "failed": 0}, "paused": False,
        "bale": {"diag": {"getMe": "ok", "getChat": "ok"}, "outage_active": False}, "tg_lane": {"sent": 8, "queued": 0, "failed_attempts": 1, "last_ok_utc": "2026-11-18 03:00", "last_error": None},
        "ai_providers": [{"name": "groq", "ok": True}, {"name": "deepseek", "ok": False}]}
async def collector(client, channel, limit, admin): return {"channel": {"title": "<کانال> & من", "username": "bale_dr", "subs": 1500}, "posts": posts, "admin": None}
async def admin_c(client, channel): return {"available": False, "reason": "ChatAdminRequiredError"}
def mkpanel(admins=(111,), **kw):
    st = mkstate(); fb = FakeBot(); clk = [NOW]
    pn = Panel(fb, admins, st, None, "@bale_dr", lambda: dict(PROJ, paused=bool(st.data.get("paused"))), collector=collector, admin_collector=admin_c, clock=lambda: clk[0], **kw)
    pn.clk = clk; pn.fb = fb; pn.st = st; return pn
def umsg(uid, text, fid=111, ctype="private", date=None): return {"update_id": uid, "message": {"text": text, "from": {"id": fid}, "chat": {"id": fid, "type": ctype}, "date": date or int(NOW)}}
def ucb(uid, data, fid=111, mid=50): return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "from": {"id": fid}, "data": data, "message": {"message_id": mid, "chat": {"id": fid}}}}

pn = mkpanel()
# همه‌ی صفحه‌ها: متن < ۴۰۹۶، callback_data ≤ ۶۴ بایت، HTML بی‌خطر
for key in ["m", "ch:1", "ch:7", "ch:30", "ch:0", "top:7:v", "top:30:r", "top:1:f", "top:0:v", "rx:7", "rx:0", "fw:30", "hr:30", "adm", "pj", "hl", "ctl", "pause", "pause_y", "resume"]:
    text, kb = run(pn.page(key))
    assert 0 < len(text) < 4096, key
    for row in kb:
        for b_ in row: assert len(b_["callback_data"].encode()) <= 64 and b_["text"], (key, b_)
    assert "<script" not in text
text, _ = run(pn.page("m")); assert "&lt;کانال&gt; &amp; من" in text and "1,500" in text                     # عنوان escape شد
text, _ = run(pn.page("top:7:v")); assert "https://t.me/bale_dr/1" in text and "پست یک" in text
text, _ = run(pn.page("rx:7")); assert "👍" in text and "█" in text
text, _ = run(pn.page("adm")); assert "ChatAdminRequiredError" in text and "ادمین" in text
text, _ = run(pn.page("pj")); assert "100" in text and "10٪" in text and "groq" not in text and "32" in text
text, _ = run(pn.page("hl")); assert "groq ✅" in text and "deepseek ❌" in text
# تزریق HTML از متن پست
evil = [dict(posts[0], id=99, text="<b>x</b><a href=1>y</a>", views=10**6)]
async def evil_c(c, ch, l, a): return {"channel": {"title": "t", "username": "u", "subs": 1}, "posts": evil, "admin": None}
pe = mkpanel(); pe.collector = evil_c; t, _ = run(pe.page("top:7:v")); assert "&lt;b&gt;x&lt;/b&gt;" in t and "<a href=1>" not in t
# ---- دسترسی
pn = mkpanel((111,))
run(pn.handle(umsg(1, "/start", fid=999))); assert pn.fb.sent == [] and pn.counters["unauthorized"] == 1            # غیرمدیر: بی‌پاسخ
run(pn.handle(umsg(2, "/id", fid=999))); assert "999" in pn.fb.sent[-1][1] and "<code>999</code>" in pn.fb.sent[-1][1]  # فقط شناسه‌ی خودش
run(pn.handle(umsg(3, "/start", fid=111, ctype="group"))); assert len(pn.fb.sent) == 1                               # گروه نادیده
run(pn.handle(umsg(4, "/start"))); chat, text, mk = pn.fb.sent[-1]; assert chat == 111 and "کنترل پنل" in text and mk["inline_keyboard"]
for cmd in ("/stats", "/top", "/project", "/health", "/menu"): run(pn.handle(umsg(5, cmd))); assert pn.fb.sent[-1][1]
run(pn.handle(umsg(6, "/bogus"))); assert "ناشناخته" in pn.fb.sent[-1][1]
run(pn.handle(ucb(7, "m", fid=999))); assert pn.fb.answers[-1] == ("cb7", "دسترسی ندارید.", True) and pn.fb.edits == []   # دکمه برای غیرمدیر
run(pn.handle(ucb(8, "ch:30", mid=77))); assert pn.fb.edits[-1][1] == 77 and "۳۰ روز" in pn.fb.edits[-1][2]
run(pn.handle(ucb(9, "rf", mid=77))); assert "۳۰ روز" in pn.fb.edits[-1][2] and pn.fb.answers[-1][1] == "به‌روز شد ✓"      # به‌روزرسانی همان صفحه
pn.fb.fail = "edit"; run(pn.handle(ucb(10, "ch:7", mid=77))); pn.fb.fail = None                                           # message not modified بی‌خطر
# ---- جمع‌آوری ناموفق: پیام خطای قابل‌فهم، بدون کرش
async def bad_c(c, ch, l, a): raise RuntimeError("flood")
pb = mkpanel(); pb.collector = bad_c; run(pb.handle(umsg(1, "/stats"))); assert "ناموفق" in pb.fb.sent[-1][1] and "RuntimeError" in pb.fb.sent[-1][1]
run(pb.handle(ucb(2, "ch:7"))); assert "خطا" in pb.fb.answers[-1][1]
pb.project_fn = lambda: PROJ; run(pb.handle(umsg(3, "/project"))); assert "فعالیت پروژه" in pb.fb.sent[-1][1]      # صفحه‌ی پروژه به آمار کانال وابسته نیست
# ---- کش
calls = []
async def counting(c, ch, l, a): calls.append(1); return await collector(c, ch, l, a)
pc = mkpanel(); pc.collector = counting; run(pc.page("ch:7")); run(pc.page("rx:7")); run(pc.page("ch:1")); assert len(calls) == 1
pc.clk[0] += 10; run(pc.page("ch:7", force=True)); assert len(calls) == 1                                                  # force زیر ۲۰ ثانیه: کش
pc.clk[0] += 30; run(pc.page("ch:7", force=True)); assert len(calls) == 2
pc.clk[0] += 400; run(pc.page("ch:7")); assert len(calls) == 3                                                              # TTL
# ---- کنترل ارسال (با تأیید)
pk = mkpanel(); t, kb = run(pk.page("ctl")); assert "فعال" in t and not pk.st.data["paused"]
t, kb = run(pk.page("pause")); assert "⚠️" in t and not pk.st.data["paused"]                                              # فقط تأیید می‌خواهد
t, kb = run(pk.page("pause_y")); assert pk.st.data["paused"] is True and "متوقف" in t and pk.st.dirty
woke = []; pk.wake = lambda: woke.append(1); t, kb = run(pk.page("resume")); assert pk.st.data["paused"] is False and woke == [1]
run(pk.handle(ucb(1, "pause_y", fid=999))); assert pk.st.data["paused"] is False                                           # غیرمدیر نمی‌تواند متوقف کند
pk.st.data["paused"] = True; pk.st.save(); assert State(pk.st.path).data["paused"] is True                                  # توقف پس از ری‌استارت می‌ماند
print("panel pages/auth OK")

# ---------------- حلقه‌ی دریافت ----------------
async def loop_for(pn, secs=0.4):
    t = asyncio.create_task(pn.run()); await asyncio.sleep(secs); pn.stop(); await asyncio.wait_for(t, 40)
pl = mkpanel(); pl.fb.updates = [umsg(10, "/start"), umsg(11, "/stats"), umsg(12, "قدیمی", date=int(NOW) - 9999), umsg(13, "/start", date=int(NOW) - 9999)]
pl.fb.get_updates_orig = pl.fb.get_updates
n_calls = []
def once(offset=0, timeout=25):
    n_calls.append(offset); r = pl.fb.get_updates_orig(offset, timeout)
    if not r: time.sleep(0.05)
    return r
pl.fb.get_updates = once; run(loop_for(pl))
assert pl.st.data["panel_offset"] == 14 and len([x for x in pl.fb.sent if isinstance(x, tuple)]) == 2                       # دستور ۲دقیقه‌ی قدیمی اجرا نشد
assert pl.fb.sent[0] == "deleteWebhook" and pl.fb.sent[1] == "setMyCommands" and 14 in n_calls                              # ack با offset
# ری‌استارت: offset ماندگار؛ دستور قبلی دوباره اجرا نمی‌شود
pl.st.save(); pl2 = mkpanel(); pl2.st = State(pl.st.path); pl2.state = pl2.st
assert pl2.state.data["panel_offset"] == 14
# 409 (مصرف‌کننده‌ی دیگر) → backoff و ادامه، بدون کرش
pe = mkpanel(); pe.fb.fail = "poll"
async def short(): 
    orig = asyncio.sleep
    t = asyncio.create_task(pe.run()); await orig(0.3); pe.stop(); t.cancel()
    try: await t
    except asyncio.CancelledError: pass
run(short()); assert pe.counters["errors"] >= 1 and pe.last_error == "http_409"
st_ = pe.status(); assert st_["admins"] == 1 and st_["last_error"] == "http_409"
print("panel loop OK")

# ---------------- build_panel ----------------
async def me_ok(): return NS(id=555)
cl = NS(get_me=me_ok)
os.environ["TG_ADMIN_IDS"] = "111, 222;abc"; os.environ["TG_DESTINATION"] = "@bale_dr"; os.environ.pop("TG_STATS_CHANNEL", None)
os.environ.pop("TG_BOT_TOKEN", None); assert run(tg_panel.build_panel(cl, mkstate(), lambda: {})) is None
os.environ["TG_BOT_TOKEN"] = "T"; bp = run(tg_panel.build_panel(cl, mkstate(), lambda: {})); assert bp.admins == {111, 222, 555} and bp.channel == "@bale_dr"   # صاحب نشست خودکار ادمین است
async def me_bad(): raise RuntimeError("x")
bp = run(tg_panel.build_panel(NS(get_me=me_bad), mkstate(), lambda: {})); assert bp.admins == {111, 222}
print("ALL TG PANEL OK")
