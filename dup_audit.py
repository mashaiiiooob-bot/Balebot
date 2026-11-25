"""ممیزی تکراری‌ها روی پست‌های واقعیِ کانال مقصد: برای هر پست، نزدیک‌ترین پستِ قبلی (تا ۶ ساعت) را با شباهت معنایی (Embedding) پیدا می‌کند
و می‌گوید روش‌های واژگانی (v1/v2) آن را تکراری می‌دانستند یا نه. خروجی: dup_audit.json (شامل ۷۰ نویسه‌ی اول متن برای بررسی؛ پست‌ها عمومی‌اند)."""
import asyncio
import json
import os
import time

from telethon import TelegramClient
from telethon.sessions import StringSession

import dedupe
import semantic


async def main():
    chat = os.environ["TG_DESTINATION"]
    client = TelegramClient(StringSession(os.environ["TG_SESSION"]), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    posts = []
    async for m in client.iter_messages(chat, limit=int(os.environ.get("N", "500"))):
        body = " ".join((m.message or "").split()).split("✍")[0].strip()
        if len(body) >= 12 and m.date:
            posts.append({"id": m.id, "ts": m.date.timestamp(), "text": body})
    await client.disconnect()
    posts.sort(key=lambda p: p["ts"])
    emb = semantic.Embedder()
    emb.init()
    vecs = emb.embed([p["text"] for p in posts])
    if not vecs:
        raise SystemExit("embedding failed: " + str(emb.probe))
    sigs = [dedupe.signature(p["text"]) for p in posts]
    ctx = dedupe.context([{"w": s["w"]} for s in sigs])
    rows = []
    for i, p in enumerate(posts):
        best = None
        for j in range(i - 1, -1, -1):
            if p["ts"] - posts[j]["ts"] > 6 * 3600:
                break
            c = semantic.cosine(vecs[i], vecs[j])
            if best is None or c > best[0]:
                best = (c, j)
        if best is None:
            continue
        c, j = best
        cmp_ = dedupe.compare(sigs[i], {"w": sigs[j]["w"], "n": sigs[j]["n"], "p": sigs[j]["p"]}, ctx)
        e = [{"t": int(posts[j]["ts"]), **sigs[j]}]
        rows.append({"id": p["id"], "older_id": posts[j]["id"], "cos": round(c, 3), "min_apart": round((p["ts"] - posts[j]["ts"]) / 60, 1),
                     "conflict": cmp_["conflict"], "mismatch": cmp_["mismatch"], "v1": dedupe.is_duplicate_v1(sigs[i], e, p["ts"]),
                     "v2": dedupe.is_duplicate(sigs[i], e, p["ts"]), "new": p["text"][:70], "old": posts[j]["text"][:70]})
    def band(lo, hi, only_noconflict=True):
        return [r for r in rows if lo <= r["cos"] < hi and not (only_noconflict and r["conflict"])]
    summary = {"posts": len(posts), "span_hours": round((posts[-1]["ts"] - posts[0]["ts"]) / 3600, 1) if posts else 0,
               "embedding": [emb.provider, emb.model, emb.dims]}
    for name, (lo, hi) in {"cos>=0.93": (0.93, 2), "0.88-0.93": (0.88, 0.93), "0.83-0.88": (0.83, 0.88), "0.78-0.83": (0.78, 0.83)}.items():
        b = band(lo, hi)
        summary[name] = {"n": len(b), "lexical_v1_would_catch": sum(r["v1"] for r in b), "lexical_v2_would_catch": sum(r["v2"] for r in b),
                         "median_minutes_apart": sorted(r["min_apart"] for r in b)[len(b) // 2] if b else None}
    worst = sorted(band(0.80, 2), key=lambda r: -r["cos"])[:45]
    with open("dup_audit.json", "w", encoding="utf-8") as f:
        json.dump({"generated_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "summary": summary, "pairs": worst}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


asyncio.run(main())
