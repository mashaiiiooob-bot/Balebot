"""گزارش وضعیت هر هوش مصنوعی «به زبان خودش».
مدل‌ها حافظه ندارند؛ پس عددهای واقعی عملکرد هر مدل (از state/status) به او داده می‌شود و او از روی همان‌ها گزارش می‌نویسد.
خروجی: ai_reports.json و ai_reports.md (فقط عدد و متن گزارش؛ بدون اسم منبع و بدون هیچ کلید)."""
import json
import os
import sys
import time

import ai as ai_mod

PROMPT = (
    "You are the AI model '{model}' (served by {provider}). You work as one of several AI components inside a Persian "
    "news-relay bot: you read Telegram news posts and help decide whether each is banned, an ad, a duplicate, or important. "
    "You do NOT remember past requests; the DATA below are REAL measurements of your own work, supplied by the system.\\n"
    "Write a status report for the project owner in PERSIAN, in the first person, 130-220 words, with exactly these four parts: "
    "۱) نقش من  ۲) عملکرد من  ۳) مشکل‌ها و محدودیت‌ها  ۴) یک پیشنهاد.\\n"
    "Rules: quote the numbers from DATA; do NOT invent numbers, events or abilities; if a value is null or missing say it is not "
    "available yet; never claim to remember individual messages; recall = share of rule-banned messages you also flagged, "
    "false_alarm = share of good messages you wrongly flagged; agreement = share of messages where two models gave the same verdict.\\n"
    'Reply with JSON only: {{"report": "..."}}\\n\\nDATA: '
)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def pct(lat, f):
    lat = sorted(lat)
    return round(lat[min(len(lat) - 1, int(len(lat) * f))]) if lat else None


def facts_for(name, order, state, status):
    """همه‌ی عددهای واقعیِ مربوط به یک مدل (و فقط عدد/برچسب‌های بی‌نام)."""
    m = (state.get("metrics") or {})
    prov = (m.get("prov") or {}).get(name) or {}
    ai_info = ((status.get("ai") or {}).get("providers") or {}).get(name) or {}
    calls, errs = prov.get("calls", 0), prov.get("err", 0)
    day_rec = state.get("ai") or {}
    cls = {c.name: c for c, _ in ai_mod.ORDER}.get(name)
    limit = int(os.environ.get(f"AI_LIMIT_{name.upper()}", "") or getattr(cls, "default_limit", 0) or 0)
    bench = (m.get("bench") or {}).get(name) or {}
    pos, neg = bench.get("tp", 0) + bench.get("fn", 0), bench.get("fp", 0) + bench.get("tn", 0)
    pairs = {}
    for key, (agree, total) in (m.get("pair") or {}).items():
        a, b = key.split("|")
        if name in (a, b) and total:
            pairs[b if a == name else a] = {"agree": agree, "of": total, "rate": round(agree / total, 2)}
    ai_stats = status.get("ai_stats") or {}
    tot = m.get("stats") or {}
    since = m.get("since")
    return {
        "model": ai_info.get("model"), "probe_at_startup": ai_info.get("probe"),
        "my_place_in_failover_order": f"{order.index(name) + 1} of {len(order)}" if name in order else None,
        "measured_since_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(since)) if since else None,
        "requests": calls, "ok": prov.get("ok", 0), "errors": errs, "error_rate": round(errs / calls, 3) if calls else None,
        "latency_ms": {"p50": pct(prov.get("lat") or [], 0.5), "p95": pct(prov.get("lat") or [], 0.95), "samples": len(prov.get("lat") or [])},
        "daily_quota": {"used_today": day_rec.get(name, 0) if day_rec.get("day") == time.strftime("%Y-%m-%d", time.gmtime()) else 0, "limit": limit},
        "this_run_counters": {k: v for k, v in ai_stats.items() if k.startswith(name)},
        "accuracy_vs_rules": {**{k: bench.get(k, 0) for k in ("tp", "fn", "fp", "tn", "err")},
                              "recall": round(bench.get("tp", 0) / pos, 2) if pos else None,
                              "false_alarm": round(bench.get("fp", 0) / neg, 2) if neg else None},
        "agreement_with_other_models": pairs,
        "system_wide_counts": {k: tot.get(k) for k in ("seen", "sent", "duplicate", "analyst_calls", "analyst_veto_confirmed",
                                                       "analyst_veto_overruled", "analyst_rescued", "low_importance", "ai_skipped")
                               if tot.get(k) is not None},
    }


def build_prompt(name, provider_label, facts):
    return PROMPT.format(model=facts.get("model") or name, provider=provider_label) + json.dumps(facts, ensure_ascii=False)


def write_outputs(result, base="ai_reports"):
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    lines = [f"# گزارش وضعیت هوش‌های مصنوعی ({result['generated_utc']} UTC)", ""]
    for name, text in result["reports"].items():
        lines += [f"## {name} ({result['facts'][name].get('model')})", "", text, ""]
    for name, err in result["errors"].items():
        lines += [f"## {name}", "", f"گزارش گرفته نشد: {err}", ""]
    with open(f"{base}.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    state, status = load("state/state.json"), load("state/status.json")
    a = ai_mod.AI()
    a.init()
    order = [p.name for p in a.providers]
    result = {"generated_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "reports": {}, "errors": {}, "facts": {}}
    for p in a.providers + a.failed:
        facts = facts_for(p.name, order, state, status)
        result["facts"][p.name] = facts
        if not p.ok:
            result["errors"][p.name] = f"سرویس در شروع پاسخ نداد ({p.probe})"
            continue
        old, p.max_out = p.max_out, 1100
        try:
            code, out = p._run(build_prompt(p.name, type(p).__name__, facts))
        finally:
            p.max_out = old
        text = out.get("report") if code == 200 and isinstance(out, dict) else None
        if isinstance(text, str) and len(text) > 40:
            result["reports"][p.name] = text.strip()
        else:
            result["errors"][p.name] = f"پاسخ نامعتبر (http {code})"
    write_outputs(result)
    print("reports:", sorted(result["reports"]), "errors:", result["errors"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
