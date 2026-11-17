#!/usr/bin/env python3
"""بررسی فقط‌خواندنی ربات تلگرام و کانال مقصد (هیچ پیامی ارسال نمی‌شود). خروجی فقط کد/وضعیت است، بدون توکن."""
import json
import os
import requests

tok = os.environ.get("TG_BOT_TOKEN", "").strip()
dest = os.environ.get("TG_DESTINATION", "").strip()
base = f"https://api.telegram.org/bot{tok}/"
out = {"configured": bool(tok and dest)}


def call(m, **p):
    try:
        r = requests.post(base + m, json=p, timeout=20)
        j = r.json()
        return j if isinstance(j, dict) else {"ok": False, "error_code": r.status_code}
    except Exception as exc:        # noqa: BLE001
        return {"ok": False, "error_code": "network:" + type(exc).__name__}

me = call("getMe")
out["getMe"] = "ok" if me.get("ok") else me.get("error_code")
out["bot_is_bot"] = bool((me.get("result") or {}).get("is_bot"))
if me.get("ok"):
    c = call("getChat", chat_id=dest)
    out["getChat"] = "ok" if c.get("ok") else f"{c.get('error_code')}:{(c.get('description') or '')[:60]}"
    if c.get("ok"):
        out["chat_type"] = c["result"].get("type")
        m = call("getChatMember", chat_id=dest, user_id=me["result"]["id"])
        if m.get("ok"):
            r = m["result"]
            out["bot_status"] = r.get("status")
            out["can_post_messages"] = r.get("can_post_messages")
        else:
            out["getChatMember"] = f"{m.get('error_code')}:{(m.get('description') or '')[:60]}"
os.makedirs("diag_out", exist_ok=True)
json.dump(out, open("diag_out/tg.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
print(json.dumps(out, ensure_ascii=False))
