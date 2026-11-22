import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai as ai_mod, ai_report

today = time.strftime("%Y-%m-%d", time.gmtime())
state = {"ai": {"day": today, "groq": 120, "gemini": 40}, "metrics": {
    "since": 1790000000,
    "prov": {"groq": {"calls": 200, "ok": 196, "err": 4, "lat": [300, 400, 500, 900, 350]},
             "gemini": {"calls": 0, "ok": 0, "err": 0, "lat": []}},
    "bench": {"groq": {"tp": 8, "fn": 16, "fp": 0, "tn": 20, "err": 1}},
    "pair": {"gemini|groq": [84, 100], "cloudflare|groq": [83, 100]},
    "stats": {"seen": 5000, "sent": 900, "analyst_calls": 400, "analyst_veto_overruled": 12}}}
status = {"ai": {"providers": {"groq": {"model": "openai/gpt-oss-120b", "probe": "ok"}}}, "ai_stats": {"groq_analyst_ok": 150, "gemini_error": 3}}

f = ai_report.facts_for("groq", ["groq", "gemini", "cloudflare"], state, status)
assert f["model"] == "openai/gpt-oss-120b" and f["requests"] == 200 and f["errors"] == 4 and f["error_rate"] == 0.02
assert f["latency_ms"]["p50"] == 400 and f["latency_ms"]["samples"] == 5
assert f["daily_quota"] == {"used_today": 120, "limit": 800}
assert f["accuracy_vs_rules"]["recall"] == 0.33 and f["accuracy_vs_rules"]["false_alarm"] == 0.0
assert f["agreement_with_other_models"] == {"gemini": {"agree": 84, "of": 100, "rate": 0.84}, "cloudflare": {"agree": 83, "of": 100, "rate": 0.83}}
assert f["this_run_counters"] == {"groq_analyst_ok": 150} and f["my_place_in_failover_order"] == "1 of 3"
assert f["system_wide_counts"]["analyst_veto_overruled"] == 12 and f["measured_since_utc"]
# مدلی که هنوز داده ندارد: مقدار null (نه صفر ساختگی)، تا مدل چیزی نسازد
g = ai_report.facts_for("gemini", ["groq", "gemini"], state, status)
assert g["error_rate"] is None and g["latency_ms"]["p50"] is None and g["accuracy_vs_rules"]["recall"] is None
# سهمیه‌ی روز قبل حساب نمی‌شود
old = json.loads(json.dumps(state)); old["ai"]["day"] = "2000-01-01"
assert ai_report.facts_for("groq", ["groq"], old, status)["daily_quota"]["used_today"] == 0
# پرامپت: دستور «نساز» و داده‌ی واقعی هر دو هستند؛ هیچ اسم منبع یا کلیدی نیست
p = ai_report.build_prompt("groq", "Groq", f)
assert "do NOT invent numbers" in p and '"requests": 200' in p and "openai/gpt-oss-120b" in p
for secret_like in ("gsk_", "AIza", "cfut_", "sk-", "token"):
    assert secret_like not in p
# خروجی‌ها
res = {"generated_utc": "t", "facts": {"groq": f}, "reports": {"groq": "گزارش " * 20}, "errors": {"deepseek": "x"}}
d = tempfile.mkdtemp(); ai_report.write_outputs(res, base=os.path.join(d, "r"))
assert json.load(open(os.path.join(d, "r.json"), encoding="utf-8"))["reports"]["groq"].startswith("گزارش")
md = open(os.path.join(d, "r.md"), encoding="utf-8").read(); assert "## groq (openai/gpt-oss-120b)" in md and "گزارش گرفته نشد" in md
print("AI REPORT OK")
