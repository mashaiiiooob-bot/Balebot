import os, sys, tempfile, threading, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from resilience.outbox import Outbox
from resilience.deliver import Deliverer, Breaker, classify
from resilience.panel import Panel
from resilience.health import evaluate, Alerter, format_status
from resilience.bridge import OutboxBale
import run_relay

class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t
    def adv(self, s): self.t += s

def mk(clock=None, **kw):
    d = tempfile.mkdtemp(); clk = clock or Clock()
    return Outbox(os.path.join(d, "o.sqlite"), clock=clk, rng=lambda: 0.5, **kw), clk, d

class FakeBale:
    def __init__(self): self.sent = []; self.fail = None; self.updates = []; self.calls = []
    def _maybe(self):
        if self.fail:
            e = self.fail; self.fail = e if getattr(e, "sticky", True) else None; raise e
    def send_text(self, chat, text): self._maybe(); self.sent.append(("text", str(chat), text))
    def send_media(self, chat, path, caption, kind):
        self._maybe(); self.sent.append(("media", str(chat), open(path, "rb").read(), caption, kind))
    def _post(self, method, data=None, files=None):
        self.calls.append((method, dict(data or {}))); off = (data or {}).get("offset", 0)
        return [u for u in self.updates if u["update_id"] >= off]

# ---------------- Outbox ----------------
ob, clk, d = mk()
assert ob.enqueue("k1", {"type": "text", "text": "a"}) and not ob.enqueue("k1", {"type": "text", "text": "a"})   # ضدتکرار
ob.enqueue("k2", {"type": "text", "text": "b"}); ob.enqueue("k3", {"type": "text", "text": "c"})
items = ob.claim(2); assert [i["payload"]["text"] for i in items] == ["a", "b"]                                    # FIFO
assert [i["payload"]["text"] for i in ob.claim(5)] == ["c"] and ob.claim(5) == []                                  # قفل: دوباره برداشته نمی‌شود
# کرش: lease تمام می‌شود → بازیابی خودکار با شمردن تلاش
clk.adv(200); r = ob.claim(10); assert sorted(i["payload"]["text"] for i in r) == ["a", "b", "c"] and all(i["attempts"] == 1 for i in r)
# ری‌استارت پروسه: نمونه‌ی جدید روی همان فایل، داده‌ها باقی است
ob2 = Outbox(ob.path, clock=clk); assert ob2.stats()["inflight"] == 3 and ob2.has("k2")
for i in r: ob.mark_sent(i["id"])
assert ob.stats()["sent"] == 3 and not ob.enqueue("k1", {"type": "text", "text": "a"})                              # بعد از ارسال هم تکراری نیست
# backoff نمایی و dead
ob, clk, d = mk(max_attempts=4); ob.enqueue("x", {"type": "text", "text": "x"}); delays = []
for n in range(3):
    it = ob.claim(1)[0]; before = clk.t; assert ob.mark_failed(it["id"], "net") == "retry"
    c = sqlite = ob._conn(); nt = c.execute("SELECT next_try FROM outbox").fetchone()[0]; c.close(); delays.append(round(nt - before)); clk.adv(4000)
assert delays == [5, 10, 20], delays
it = ob.claim(1)[0]; assert ob.mark_failed(it["id"], "net") == "dead" and ob.stats()["dead"] == 1                 # بعد از max_attempts
assert ob.retry_dead() == 1 and ob.stats()["pending"] == 1 and ob.dead_items() == []
assert "text" not in json.dumps(ob.dead_items())
# retry_after احترام گذاشته می‌شود
it = ob.claim(1)[0]; before = clk.t; ob.mark_failed(it["id"], "429", retry_after=500)
assert ob.claim(1) == []; clk.adv(499); assert ob.claim(1) == []; clk.adv(2); assert len(ob.claim(1)) == 1
# رسانه: کپی در spool، حذف بعد از ارسال، ضدتکرار با محتوای یکسان
ob, clk, d = mk(); f = os.path.join(d, "p.jpg"); open(f, "wb").write(b"IMG")
assert ob.enqueue_media("m1", f, "cap", "photo") and not ob.enqueue_media("m1", f, "cap", "photo")
os.remove(f); it = ob.claim(1)[0]; sp = ob.spool_path(it["payload"]); assert open(sp, "rb").read() == b"IMG"      # فایل اصلی پاک شد؛ spool باقی
ob.mark_sent(it["id"]); assert not os.path.exists(sp)
# همروندی: دو thread همزمان claim → هیچ آیتمی دوبار برداشته نمی‌شود
ob, clk, d = mk()
for i in range(200): ob.enqueue(f"c{i}", {"type": "text", "text": str(i)})
got = []; lock = threading.Lock()
def worker():
    while True:
        b = ob.claim(7)
        if not b: return
        with lock: got.extend(x["id"] for x in b)
ts = [threading.Thread(target=worker) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
assert len(got) == 200 and len(set(got)) == 200
# purge
ob, clk, d = mk(); ob.enqueue("o", {"type": "text", "text": "o"}); ob.mark_sent(ob.claim(1)[0]["id"]); clk.adv(8 * 86400)
assert ob.purge() == 1 and ob.stats()["sent"] == 0
print("outbox OK")

# ---------------- classify / breaker ----------------
assert classify(RuntimeError("Bale HTTP 400 on sendMessage")) == (True, 0, "http_400")
assert classify(RuntimeError("Bale HTTP 429 on sendMessage"))[0] is False and classify(RuntimeError("Bale HTTP 429 on x"))[1] == 30
assert classify(RuntimeError("Bale HTTP 403 on x")) == (False, 300, "http_403")
assert classify(RuntimeError("Bale HTTP 502 on x")) == (False, 0, "http_502")
assert classify(RuntimeError("Bale network error on x: ConnectTimeout")) == (False, 0, "network")
clk = Clock(); b = Breaker(threshold=3, open_s=60, clock=clk)
for _ in range(2): b.fail()
assert b.allow(); b.fail(); assert not b.allow() and b.is_open
clk.adv(61); assert b.allow(); b.fail(); assert not b.allow()                  # پروب ناموفق → دوباره باز، مدت دو برابر
clk.adv(100); assert not b.allow(); clk.adv(30); assert b.allow(); b.ok(); assert b.fails == 0
print("breaker OK")

# ---------------- Deliverer ----------------
ob, clk, d = mk(); bale = FakeBale(); sleeps = []
dl = Deliverer(ob, bale, "@dest", Breaker(threshold=2, open_s=60, clock=clk), rate_per_s=2, sleep=sleeps.append, clock=clk)
for i in range(3): ob.enqueue(f"d{i}", {"type": "text", "text": f"m{i}"})
r = dl.run_once(); assert r["sent"] == 3 and [s[2] for s in bale.sent] == ["m0", "m1", "m2"] and sleeps == [0.5, 0.5]
assert dl.run_once()["sent"] == 0 and len(bale.sent) == 3                      # دوباره نمی‌فرستد (بدون تکرار)
# قطع اینترنت: همه در صف می‌مانند، breaker باز می‌شود، چیزی گم نمی‌شود
for i in range(5): ob.enqueue(f"e{i}", {"type": "text", "text": f"n{i}"})
bale.fail = RuntimeError("Bale network error on sendMessage: ConnectTimeout"); bale.sent.clear()
r = dl.run_once(); assert r["sent"] == 0 and r["retry"] == 1 and r["released"] == 4
clk.adv(10); r = dl.run_once(); assert r["retry"] == 1                           # backoff اول (≈۵ثانیه) تمام شده؛ دوباره شکست → breaker باز
assert dl.run_once()["skipped"] == "breaker_open" and ob.stats()["pending"] == 5 and ob.stats()["dead"] == 0
# بازگشت اینترنت: بعد از بسته شدن breaker و backoff همه ارسال می‌شود
bale.fail = None; clk.adv(3600); r = dl.run_once(20); assert r["sent"] == 5 and ob.stats()["pending"] == 0 and len(bale.sent) == 5
# خطای دائمی ۴۰۰ → dead، بقیه ادامه می‌یابند
ob.enqueue("bad", {"type": "text", "text": "bad"}); ob.enqueue("good", {"type": "text", "text": "good"}); orig = bale.send_text
def picky(chat, text):
    if text == "bad": raise RuntimeError("Bale HTTP 400 on sendMessage")
    orig(chat, text)
bale.send_text = picky; bale.sent.clear(); r = dl.run_once(); assert r["dead"] == 1 and r["sent"] == 1 and bale.sent[0][2] == "good"
# توکن اشتباه (403): dead نمی‌شود؛ پس از اصلاح تحویل می‌شود
ob.enqueue("t403", {"type": "text", "text": "keep"}); bale.send_text = lambda c, t: (_ for _ in ()).throw(RuntimeError("Bale HTTP 403 on sendMessage"))
dl.breaker.ok(); dl.run_once(); assert ob.stats()["dead"] == 1 and ob.stats()["pending"] == 1
bale.send_text = orig; dl.breaker.ok(); clk.adv(4000); assert dl.run_once()["sent"] == 1
# pause
ob.enqueue("p", {"type": "text", "text": "p"}); ob.kv_set("paused", "1"); assert dl.run_once()["skipped"] == "paused"; ob.kv_set("paused", "0")
# رسانه + spool گمشده
f = os.path.join(d, "a.mp4"); open(f, "wb").write(b"VID"); ob.enqueue_media("mv", f, "cap", "video", "@dest")
bale.sent.clear(); assert dl.run_once()["sent"] == 2 and ("media", "@dest", b"VID", "cap", "video") in bale.sent
ob.enqueue_media("mv2", f, "c2", "video"); it = ob.claim(1)[0]; os.remove(ob.spool_path(it["payload"])); ob.release([it["id"]])
assert dl.run_once()["dead"] == 1
# ضربان کلکتور ثبت می‌شود و به بله ارسال نمی‌شود
ob.enqueue("hb", {"type": "heartbeat", "ts": 1234.0}); bale.sent.clear(); dl.run_once(); assert ob.kv_get("collector_last_seen") == "1234.0" and bale.sent == []
print("deliver OK")

# ---------------- Panel ----------------
ob, clk, d = mk(); bale = FakeBale(); br = Breaker(clock=clk); panel = Panel(ob, bale, ["111"], br)
upd = lambda uid, text, fid=111, cid=111: {"update_id": uid, "message": {"text": text, "from": {"id": fid}, "chat": {"id": cid}}}
assert panel.handle(upd(1, "/status", fid=999, cid=999)) is None                 # غیرمدیر: بی‌پاسخ
assert panel.handle(upd(1, "/status", fid=111, cid=555)) is None                 # مدیر ولی در گروه: بی‌پاسخ
assert panel.handle(upd(1, "سلام")) is None
assert "صف" in panel.handle(upd(1, "/status"))[1]
assert "متوقف" in panel.handle(upd(2, "/pause"))[1] and ob.is_paused(); assert "ادامه" in panel.handle(upd(3, "/resume@MyBot"))[1] and not ob.is_paused()
ob.enqueue("z", {"type": "text", "text": "SECRET-TEXT"}); it = ob.claim(1)[0]; ob.mark_failed(it["id"], "http_400", permanent=True)
dead = panel.handle(upd(4, "/dead"))[1]; assert "http_400" in dead and "SECRET-TEXT" not in dead
assert "1" in panel.handle(upd(5, "/retry"))[1] and ob.stats()["pending"] == 1
# poll: offset ماندگار؛ بعد از «ری‌استارت» دستور قبلی دوباره اجرا نمی‌شود
bale.updates = [upd(10, "/pause"), upd(11, "/status"), upd(12, "/hack", fid=7, cid=7)]
assert panel.poll_once() == 2 and ob.kv_get("panel_offset") == "13" and ob.is_paused()
bale.sent.clear(); panel2 = Panel(Outbox(ob.path, clock=clk), bale, ["111"]); assert panel2.poll_once() == 0 and bale.calls[-1][1]["offset"] == 13 and bale.sent == []
print("panel OK")

# ---------------- Health ----------------
ob, clk, d = mk(); assert evaluate(ob, clk()) == []
ob.enqueue("h", {"type": "text", "text": "h"}); clk.adv(1000); codes = [c for c, _ in evaluate(ob, clk())]; assert "backlog" in codes
ob.kv_set("collector_last_seen", clk() - 2000); assert "collector_silent" in [c for c, _ in evaluate(ob, clk())]
assert "breaker" in [c for c, _ in evaluate(ob, clk(), breaker_open=True)]
al = Alerter(ob, cooldown_s=1800, clock=clk); due = al.due(evaluate(ob, clk())); assert due
for c, _ in due: al.mark(c)
assert al.due(evaluate(ob, clk())) == []; clk.adv(1801); assert al.due(evaluate(ob, clk()))                         # cooldown
assert "کلکتور" in format_status(ob, now=clk())
print("health OK")

# ---------------- Bridge (آداپتور poll.py) ----------------
ob, clk, d = mk(); real = FakeBale(); real.get_me = lambda: {"id": 1}; br = OutboxBale(ob, real, clock=clk)
assert br.send_text("@c", "hello") and not br.send_text("@c", "hello") and br.send_text("@c", "other")
f = os.path.join(d, "x.png"); open(f, "wb").write(b"PNG"); assert br.send_media("@c", f, "cap", "photo") and not br.send_media("@c", f, "cap", "photo")
assert ob.stats()["pending"] == 3 and br.get_me() == {"id": 1}
assert br.heartbeat() and not br.heartbeat(); clk.adv(61); assert br.heartbeat()
dl = Deliverer(ob, real, "@c", clock=clk, sleep=lambda s: None); dl.run_once(20)
assert [s[0] for s in real.sent] == ["text", "text", "media"] and ob.kv_get("collector_last_seen") is not None
print("bridge OK")

# ---------------- Relay (یکپارچه) ----------------
ob, clk, d = mk(); bale = FakeBale(); relay = run_relay.Relay(ob, bale, "@dest", ["111"], rate=0, clock=clk, sleep=lambda s: None)
ob.enqueue("r", {"type": "text", "text": "x"}); ob.kv_set("collector_last_seen", clk() - 5000)
assert relay.step()["sent"] == 1 and relay.check_health() == 1                       # هشدار «کلکتور ساکت» به مدیر
assert relay.check_health() == 0                                                      # cooldown: اسپم نمی‌شود
bale.fail = RuntimeError("Bale network error on sendMessage: x"); clk.adv(4000); ob.enqueue("r2", {"type": "text", "text": "y"})
assert relay.check_health() == 0 and ob.stats()["pending"] == 1                       # بله قطع: هشدار ارسال نمی‌شود اما چیزی گم نمی‌شود
print("ALL RESILIENCE OK")

# ---------------- BaleClient: جزئیات خطای رسمی (error_code / parameters.retry_after) ----------------
import requests as _rq
from bale_client import BaleClient, BaleError
class _Resp:
    def __init__(self, code, body=None, bad_json=False): self.status_code = code; self._b = body; self._bad = bad_json
    def raise_for_status(self):
        if self.status_code >= 400:
            e = _rq.HTTPError("http://secret-url/botTOKEN/x"); e.response = self; raise e
    def json(self):
        if self._bad: raise ValueError("no json")
        return self._b
class _Sess:
    def __init__(self, r): self.r = r
    def post(self, *a, **k): return self.r
def _client(r):
    c = BaleClient("TOKEN123"); c.session = _Sess(r); return c
try: _client(_Resp(429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}}))._post("sendMessage")
except BaleError as e:
    assert e.retry_after == 7 and e.error_code == 429 and e.status == 429 and str(e) == "Bale HTTP 429 on sendMessage" and "TOKEN123" not in str(e)
    assert isinstance(e, RuntimeError) and classify(e) == (False, 30, "http_429")                       # حداقل ۳۰ و نه کمتر از پیشنهاد سرور
else: raise AssertionError
try: _client(_Resp(429, {"ok": False, "parameters": {"retry_after": 120}}))._post("x")
except BaleError as e: assert classify(e) == (False, 120, "http_429")                                       # پیشنهاد سرور بزرگ‌تر → همان
try: _client(_Resp(500, bad_json=True))._post("x")
except BaleError as e: assert e.retry_after == 0 and e.error_code is None and str(e) == "Bale HTTP 500 on x"
try: _client(_Resp(200, {"ok": False, "error_code": 400, "parameters": {"retry_after": "x"}}))._post("x")
except BaleError as e: assert e.error_code == 400 and e.retry_after == 0 and "rejected" in str(e)
assert _client(_Resp(200, {"ok": True, "result": [1]}))._post("getUpdates") == [1]
# retry_after واقعی تا backoff صف منتقل می‌شود
ob, clk, d = mk(); ob.enqueue("rt", {"type": "text", "text": "q"}); bale = FakeBale()
bale.fail = BaleError("Bale HTTP 429 on sendMessage", status=429, retry_after=300)
dl = Deliverer(ob, bale, "@d", Breaker(clock=clk), sleep=lambda s: None, clock=clk); dl.run_once()
clk.adv(299); assert ob.claim(1) == []; clk.adv(2); assert len(ob.claim(1)) == 1
print("BALE ERROR DETAILS OK")
