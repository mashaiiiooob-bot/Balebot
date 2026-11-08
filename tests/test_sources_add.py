import os, sys, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import settings
def load(**env):
    for k in ("SOURCES","SOURCES_EXTRA","SOURCES_REMOVE","SOURCES_ADD"): os.environ.pop(k, None)
    os.environ.update(env); importlib.reload(settings); return settings
s = load(SOURCES="https://t.me/aaaa1111\nhttps://t.me/DrTel", SOURCES_ADD="https://t.me/good_source_three\nhttps://t.me/drtel")
assert [x.lower() for x in s.get_sources()] == ["https://t.me/aaaa1111", "https://t.me/drtel", "https://t.me/good_source_three"], s.get_sources()  # drtel تکراری نیست
# اگر SOURCES_REMOVE آن‌ها را حذف کرده باشد، ADD برنده است
s = load(SOURCES="https://t.me/aaaa1111\nhttps://t.me/drtel", SOURCES_REMOVE="drtel\ngood_source_three", SOURCES_ADD="https://t.me/good_source_three\nhttps://t.me/drtel")
got = [x.lower() for x in s.get_sources()]
assert got == ["https://t.me/aaaa1111", "https://t.me/good_source_three", "https://t.me/drtel"], got
assert s.get_add_terms() == {s.confuse("good_source_three"), s.confuse("drtel")}
# بدون ADD رفتار قبلی: REMOVE اثر می‌کند
s = load(SOURCES="https://t.me/aaaa1111\nhttps://t.me/drtel", SOURCES_REMOVE="drtel")
assert s.get_sources() == ["https://t.me/aaaa1111"]
for k in ("SOURCES","SOURCES_EXTRA","SOURCES_REMOVE","SOURCES_ADD"): os.environ.pop(k, None)
print("SOURCES_ADD OK")
