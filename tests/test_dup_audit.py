import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dup_audit

def msg(i, text, when="2026-11-25T07:40:00+00:00", extra=""):
    return f'''<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="bale_dr/{i}">
<div class="tgme_widget_message_bubble"><div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>{extra}
<div class="tgme_widget_message_footer"><a class="tgme_widget_message_date"><time datetime="{when}" class="time">10:10</time></a></div></div></div></div>'''
page = msg(101, "حمله پهپادی به اصفهان؛ دو نفر زخمی شدند<br/><b>فوری</b> &amp; مهم") + msg(102, "کوتاه") + \
       msg(103, "خبر دیگر با امضا<br/><br/>✍️  F16<br/><br/>⚔️ @Bale_Dr", "2026-11-25T08:00:00+00:00") + \
       '<div class="tgme_widget_message_wrap"><div data-post="bale_dr/104"></div></div>'   # بدون متن/زمان: نادیده
posts = dup_audit.parse_page(page)
assert [p["id"] for p in posts] == [101, 103]                       # «کوتاه» (<۱۲ نویسه) و پست بدون متن حذف
assert posts[0]["text"] == "حمله پهپادی به اصفهان؛ دو نفر زخمی شدند فوری & مهم"   # br→فاصله، تگ حذف، entity باز شد
assert posts[1]["text"] == "خبر دیگر با امضا"                        # امضا بریده شد
assert abs(posts[1]["ts"] - posts[0]["ts"] - 1200) < 1               # ۲۰ دقیقه بعد
# صفحه‌بندی: before=کوچک‌ترین id؛ توقف وقتی کافی شد یا صفحه خالی بود
calls = []
class R:
    def __init__(self, t): self.text = t
def fake_get(url, timeout=0, headers=None):
    calls.append(url)
    return R(msg(50, "پست شماره پنجاه برای آزمایش صفحه‌بندی") + msg(40, "پست شماره چهل برای آزمایش صفحه‌بندی")) if "before=" not in url else R(msg(30, "پست شماره سی برای آزمایش صفحه‌بندی"))
got = dup_audit.fetch_posts("bale_dr", limit=3, get=fake_get)
assert len(got) == 3 and calls[1].endswith("?before=40")
print("DUP AUDIT OK")
