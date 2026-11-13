"""情報源を取得して data/items.json と data/health.json を更新する。

使い方: python scripts/update.py [--sources config/sources.json]
環境変数 SHIEN_SOURCE_OVERRIDE に JSON を渡すと、情報源のURLを差し替えられる（テスト用）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import ROOT, classify, decode, fetch, item_id, load_json, now_jst, parse_feed, parse_html_list, save_json  # noqa: E402

DATA = ROOT / "data"


def collect(source: dict) -> list[dict]:
    body, headers = fetch(source["url"])
    text = decode(body, headers)
    if source["type"] == "rss":
        return parse_feed(text)
    if source["type"] == "html_list":
        return parse_html_list(text, source["url"], source["item_href"], source.get("strip_prefixes", []), source.get("exclude_href"))
    raise ValueError(f"未知の種類: {source['type']}")


def run(sources_path: Path, data_dir: Path = DATA, today: date | None = None) -> dict:
    cfg = load_json(sources_path, {})
    rules = load_json(ROOT / "config" / "rules.json", {})
    override = json.loads(os.environ.get("SHIEN_SOURCE_OVERRIDE", "{}"))
    items_path = data_dir / "items.json"
    health_path = data_dir / "health.json"
    store = load_json(items_path, {"items": []})
    health = load_json(health_path, {"sources": {}})
    by_id = {it["id"]: it for it in store["items"]}
    now = now_jst()
    today = today or now.date()
    summary = {"new": 0, "sources": {}}

    for src in cfg["sources"]:
        src = {**src, **override.get(src["id"], {})}
        h = health["sources"].setdefault(src["id"], {"name": src["name"], "failures": 0})
        h["name"] = src["name"]
        h["url"] = src["url"]
        try:
            got = collect(src)
            if not got:
                # 0件は「ページの作りが変わって読めなくなった」可能性が高いので失敗として扱う
                raise RuntimeError("1件も読み取れませんでした（ページの作りが変わった可能性）")
            new = 0
            for g in got:
                iid = item_id(g["url"])
                c = classify(g["title"], rules, g.get("category", ""), src.get("default_topics", []))
                if iid in by_id:
                    it = by_id[iid]
                    if it["title"] != g["title"]:
                        it["title_history"] = (it.get("title_history") or []) + [it["title"]]
                        it["title"] = g["title"]
                    it["last_seen"] = now.isoformat(timespec="seconds")
                    it.update(c)
                    continue
                by_id[iid] = {
                    "id": iid,
                    "source": src["id"],
                    "source_name": src["name"],
                    "title": g["title"],
                    "url": g["url"],
                    "published": g["date"],
                    "category": g.get("category", ""),
                    "first_seen": now.isoformat(timespec="seconds"),
                    "last_seen": now.isoformat(timespec="seconds"),
                    "publish": src.get("publish", "review"),
                    **c,
                }
                new += 1
            h.update({"failures": 0, "last_ok": now.isoformat(timespec="seconds"), "last_count": len(got), "last_error": None})
            summary["sources"][src["id"]] = {"ok": True, "count": len(got), "new": new}
            summary["new"] += new
        except Exception as e:  # noqa: BLE001 情報源ごとに失敗を記録して続ける
            h["failures"] = h.get("failures", 0) + 1
            h["last_error"] = str(e)[:300]
            h["last_fail"] = now.isoformat(timespec="seconds")
            summary["sources"][src["id"]] = {"ok": False, "error": str(e)[:300]}
        time.sleep(float(os.environ.get("SHIEN_SLEEP", "2")))

    # 規則（rules.json）を変えたときに、もう一覧から消えた古い記事にも新しい規則を当てる
    srcs = {s["id"]: s for s in cfg["sources"]}
    for it in by_id.values():
        it.update(classify(it["title"], rules, it.get("category", ""), srcs.get(it.get("source"), {}).get("default_topics", [])))

    # 古いものを捨てる（掲載日がない場合は最初に見た日で判断）
    limit = today - timedelta(days=rules.get("max_age_days", 120))
    kept = []
    for it in by_id.values():
        d = it.get("published") or it["first_seen"][:10]
        if d >= limit.isoformat():
            kept.append(it)
    kept.sort(key=lambda x: (x.get("published") or x["first_seen"][:10], x["first_seen"]), reverse=True)
    store = {"updated": now.isoformat(timespec="seconds"), "items": kept}
    health["checked"] = now.isoformat(timespec="seconds")
    save_json(items_path, store)
    save_json(health_path, health)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=str(ROOT / "config" / "sources.json"))
    args = ap.parse_args()
    s = run(Path(args.sources))
    print(json.dumps(s, ensure_ascii=False, indent=1))
    # すべての情報源が失敗したときだけ異常終了にする（一部の失敗はサイトに表示して続ける）
    if s["sources"] and not any(v["ok"] for v in s["sources"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
