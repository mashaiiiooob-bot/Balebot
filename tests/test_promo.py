import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import settings
from state import State
from promo import promo_loop


class B:
    def __init__(self, fail_send=False, fail_delete=False):
        self.sent, self.deleted, self.n = [], [], 100
        self.fail_send, self.fail_delete = fail_send, fail_delete

    def send_text(self, chat, text):
        if self.fail_send: raise RuntimeError("down")
        self.n += 1; self.sent.append((chat, text, self.n)); return {"message_id": self.n}

    def delete_message(self, chat, mid):
        if self.fail_delete: raise RuntimeError("nope")
        self.deleted.append((chat, mid))


def run(bale, state, seconds, interval=0.4, visible=0.2):
    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(promo_loop(bale, state, stop, "@dest", "آیدی ما در تلگرام : @Bale_Dr", interval, visible))
        await asyncio.sleep(seconds); stop.set(); await asyncio.wait_for(task, 5)
    asyncio.run(go())

# ۱) پیش‌فرض خاموش و متن درست
assert settings.PROMO_LOOP == "off" and settings.PROMO_TEXT == "آیدی ما در تلگرام : @Bale_Dr"
assert settings.PROMO_INTERVAL == 60 and settings.PROMO_VISIBLE == 50

# ۲) چرخه: بارها می‌فرستد و هر کدام را پاک می‌کند؛ بعد از توقف هیچ پیامی نمی‌ماند
st = State(tempfile.mkdtemp() + "/s.json"); b = B()
run(b, st, 1.3)
assert len(b.sent) >= 3, b.sent
assert all(t == "آیدی ما در تلگرام : @Bale_Dr" and c == "@dest" for c, t, _ in b.sent)
assert sorted(m for _, m in b.deleted) == sorted(m for _, _, m in b.sent)   # همه پاک شدند، فقط پیام‌های خودش
assert "promo" not in st.data                                               # چیزی در state نمانده

# ۳) توقف وسط نمایش: پیام در حال نمایش هم پاک می‌شود
st = State(tempfile.mkdtemp() + "/s.json"); b = B()
run(b, st, 0.1, interval=5, visible=4)
assert len(b.sent) == 1 and b.deleted == [("@dest", b.sent[0][2])]

# ۴) قطع ناگهانی (پاک نشد) → اجرای بعدی اول همان را پاک می‌کند
st = State(tempfile.mkdtemp() + "/s.json"); st.data["promo"] = {"mid": 777}
b = B(); run(b, st, 0.1, interval=5, visible=4)
assert b.deleted[0] == ("@dest", 777)

# ۵) شکست ارسال: کرش نمی‌کند، چیزی پاک نمی‌شود، حلقه با توقف تمام می‌شود
b = B(fail_send=True); run(b, State(tempfile.mkdtemp() + "/s.json"), 0.5)
assert b.sent == [] and b.deleted == []

# ۶) شکست حذف: کرش نمی‌کند و شناسه در state می‌ماند تا اجرای بعدی پاکش کند
st = State(tempfile.mkdtemp() + "/s.json"); b = B(fail_delete=True)
run(b, st, 0.1, interval=5, visible=1)
assert st.data.get("promo", {}).get("mid") == b.sent[0][2]
print("PROMO OK")
