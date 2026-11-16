#!/usr/bin/env python3
"""بررسی فقط‌خواندنی مقصد بله (هیچ پیامی ارسال نمی‌شود): getMe، getChat و وضعیت عضویت ربات برای چند نام محتمل.
خروجی فقط کد وضعیت و واژه‌ی وضعیت است (بدون توکن، بدون شناسه‌ی عددی)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bale_client import BaleClient  # noqa: E402


def code(exc):
    m = re.search(r"Bale HTTP (\d+)", str(exc))
    return f"http_{m.group(1)}" if m else ("rejected" if "rejected" in str(exc) else "network")


def main():
    token = os.environ.get("BALE_BOT_TOKEN", "").strip()
    dest = os.environ.get("BALE_DESTINATION", "").strip()
    extra = [x.strip() for x in os.environ.get("DIAG_CHATS", "").split(",") if x.strip()]
    cands = list(dict.fromkeys([c for c in [dest, dest.lower(), dest.lstrip("@"), "@" + dest.lstrip("@")] + extra if c]))
    c = BaleClient(token)
    out = {"getMe": None, "bot_is_bot": None, "chats": {}}
    me = None
    try:
        me = c._post("getMe")
        out["getMe"] = "ok"
        out["bot_is_bot"] = bool((me or {}).get("is_bot"))
    except Exception as exc:       # noqa: BLE001
        out["getMe"] = code(exc)
    for cand in cands:
        r = {}
        try:
            info = c._post("getChat", {"chat_id": cand})
            r["getChat"] = "ok"
            r["type"] = (info or {}).get("type")
            r["has_username"] = bool((info or {}).get("username"))
        except Exception as exc:   # noqa: BLE001
            r["getChat"] = code(exc)
        if me and r["getChat"] == "ok":
            try:
                m = c._post("getChatMember", {"chat_id": cand, "user_id": me["id"]}) or {}
                r["bot_status"] = m.get("status")
                for k in ("can_post_messages", "can_send_messages", "can_edit_messages"):
                    if k in m:
                        r[k] = m[k]
            except Exception as exc:   # noqa: BLE001
                r["getChatMember"] = code(exc)
        out["chats"][("<destination>" if cand == dest else cand)] = r
    os.makedirs("diag_out", exist_ok=True)
    with open("diag_out/diag.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
