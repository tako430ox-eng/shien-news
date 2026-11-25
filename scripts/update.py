"""情報源を取得して data/items.json と data/health.json を更新する。

使い方: python scripts/update.py [--sources config/sources.json] [--data-dir data]
環境変数 SHIEN_SOURCE_OVERRIDE に JSON を渡すと、情報源のURLを差し替えられる（テスト用）。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import (ROOT, classify, clean, decode, entry_identifier, fetch, load_json,  # noqa: E402
                 notice_key, notice_url, now_jst, parse_feed, parse_html_list, save_json, tidy)

DATA = ROOT / "data"


def allowed_link(url: str, source: dict) -> bool:
    """サイトに載せてよいリンクか。http(s) 以外（javascript: や data: など）は、押すとこのサイトの中で
    プログラムが動くおそれがあるので捨てる。item_href があれば、RSS でもその形のURLだけを残す。"""
    if not isinstance(url, str) or urlsplit(url.strip()).scheme.lower() not in ("http", "https"):
        return False
    pattern = source.get("item_href")
    return not pattern or bool(re.search(pattern, url.strip()))


def collect(source: dict) -> list[dict]:
    body, headers = fetch(source["url"])
    text = decode(body, headers)
    if source["type"] == "rss":
        got = parse_feed(text, source["url"])
    elif source["type"] == "html_list":
        got = parse_html_list(text, source["url"], source["item_href"], source.get("strip_prefixes", []), source.get("exclude_href"))
    else:
        raise ValueError(f"未知の種類: {source['type']}")
    return [g for g in got if allowed_link(g.get("url"), source)]


def prepare_entries(source: str, got: list[dict]) -> list[dict]:
    """保存前に全件を検証。同一GUIDの競合を『最後の1行が正しい』とは扱わない。"""
    unique = {}
    for raw in got:
        g = dict(raw)
        if not isinstance(g.get("title"), str) or not isinstance(g.get("url"), str):
            raise ValueError("見出し・URLが文字列ではありません")
        g["title"], g["url"] = tidy(g["title"]), g["url"].strip()
        if not g["title"] or not g["url"]:
            raise ValueError("見出し・URLが空です")
        d = g.get("date")
        if d is not None and (not isinstance(d, str) or date.fromisoformat(d).isoformat() != d):
            raise ValueError("掲載日はYYYY-MM-DDまたはnullにしてください")
        g["date"] = d
        g["category"] = g.get("category") or ""
        if not isinstance(g["category"], str) or not isinstance(g.get("entry_id", ""), str):
            raise ValueError("カテゴリ・告知IDが文字列ではありません")
        native = entry_identifier(g)
        if native:
            g["entry_id"] = native
        else:
            g.pop("entry_id", None)
        key = notice_key(source, g)
        previous = unique.get(key)
        if previous:
            if notice_key(source, previous, use_entry_id=False) != notice_key(source, g, use_entry_id=False):
                raise ValueError("同一の告知IDに異なる見出し・URL・掲載日があります。情報源全体の更新を保留しました")
            categories = {x["category"] for x in (previous, g) if x["category"]}
            if len(categories) > 1:
                raise ValueError("同一のお知らせに異なるカテゴリがあります。情報源全体の更新を保留しました")
            # 表示文字の空白・全半角の違いは履歴にしない。取得順で結果を変えない。
            chosen = min((previous, g), key=lambda x: (x["title"], x["url"]))
            unique[key] = {**chosen, "category": next(iter(categories), "")}
        else:
            unique[key] = g
    # ID付きとIDなしの完全重複行が混在しても1件。異なる固有ID同士は統合しない。
    native_signatures = {}
    for g in unique.values():
        if entry_identifier(g):
            signature = notice_key(source, g, use_entry_id=False)
            native_signatures.setdefault(signature, []).append(g)
    for g in unique.values():
        if entry_identifier(g):
            continue
        matches = native_signatures.get(notice_key(source, g, use_entry_id=False), [])
        if g["category"] and any(x["category"] and x["category"] != g["category"] for x in matches):
            raise ValueError("ID付きとIDなしの重複行でカテゴリが食い違います。更新を保留しました")
        if len(matches) == 1 and not matches[0]["category"]:
            matches[0]["category"] = g["category"]
    return [g for _, g in sorted(unique.items())
            if entry_identifier(g) or notice_key(source, g, use_entry_id=False) not in native_signatures]


def merge_source(by_id: dict, legacy_items: list, shared_urls: set, src: dict,
                 got: list[dict], rules: dict, stamp: str) -> tuple[dict, list, set, dict]:
    """情報源単位のトランザクション。途中の例外では元の辞書を一切変えない。"""
    sid = src["id"]
    incoming = prepare_entries(sid, got)
    staged = copy.deepcopy(by_id)
    archive = copy.deepcopy(legacy_items)
    shared = set(shared_urls)
    stats = {"new": 0, "migrated": 0, "archived": 0, "unique_count": len(incoming)}

    for iid, it in list(staged.items()):
        if it.get("source") != sid or it.get("identity_key"):
            continue
        history = it.get("title_history") or []
        if any(clean(old) != clean(it["title"]) for old in history):
            # 旧履歴は別告知を含む。日時・GUIDがないので訂正や新記事と断定しない。
            if it not in archive:
                archive.append(copy.deepcopy(it))
            shared.add(notice_url(it["url"]))
            del staged[iid]
            stats["archived"] += 1
        else:
            # 対応が明確な既存行はID・初回取得時刻・手動の掲載判断を維持する。
            it["identity_key"] = notice_key(sid, it, use_entry_id=False)
            stats["migrated"] += 1

    by_key = {}
    by_signature = {}
    for it in staged.values():
        if it.get("source") != sid:
            continue
        key = it["identity_key"]
        if key in by_key:
            raise ValueError("保存データに同じ識別キーが複数あります。自動統合せず更新を保留しました")
        by_key[key] = it
        by_signature.setdefault(notice_key(sid, it, use_entry_id=False), []).append(it)

    for g in incoming:
        native = entry_identifier(g)
        key = notice_key(sid, g)
        signature = notice_key(sid, g, use_entry_id=False)
        it = by_key.get(key)
        if native and it is not None and g["date"] is None:
            signature = notice_key(sid, {**g, "date": it.get("published")}, use_entry_id=False)
        if native and it is not None and any(
                other is not it and not entry_identifier(other) for other in by_signature.get(signature, [])):
            # GUIDが一時的に消えた間の候補と訂正後の記事が重なるケース。
            # 手動掲載判断や説明の参照IDを捨てて統合せず、二重化する前に知らせる。
            raise ValueError("固有IDの訂正先が既存のIDなし候補と重なります。対応を確認するまで更新を保留しました")
        if it is None:
            candidates = by_signature.get(signature, [])
            # 新しく取得できたGUIDを旧行へ追加できるのは、厳密に一致する1件だけ。
            # すでに別のGUIDを持つ行を、同じ見出しという理由でまとめない。
            if native:
                candidates = [x for x in candidates if not entry_identifier(x)]
            if len(candidates) > 1:
                raise ValueError("告知IDのない行の対応先が複数あります。情報源全体の更新を保留しました")
            if candidates:
                it = candidates[0]

        if it is None:
            iid = key[:20]
            if iid in staged:
                raise ValueError("記事IDが衝突しました。既存データを上書きせず更新を保留しました")
            publish = src.get("publish", "review")
            if any(old.get("source") == sid and notice_url(old["url"]) == notice_url(g["url"])
                   and old.get("publish") != "auto" for old in archive):
                publish = "review"  # 旧行にある非公開の判断を自動解除しない
            it = {"id": iid, "source": sid, "source_name": src["name"],
                  "first_seen": stamp, "publish": publish, "identity_key": key}
            staged[iid] = it
            stats["new"] += 1
        else:
            old_signature = notice_key(sid, it, use_entry_id=False)
            by_signature[old_signature].remove(it)
            if clean(it["title"]) != clean(g["title"]):
                history = it.get("title_history") or []
                if clean(it["title"]) not in {clean(t) for t in history}:
                    it["title_history"] = history + [it["title"]]
            if native and not entry_identifier(it):
                by_key.pop(it["identity_key"], None)
                it["identity_key"] = key

        if native:
            it["entry_id"] = native
        published = g["date"] if g["date"] is not None else it.get("published")
        it.update({"title": g["title"], "url": g["url"], "published": published,
                   "category": g["category"], "last_seen": stamp})
        it.update(classify(it["title"], rules, it["category"], src.get("default_topics", [])))
        by_key[it["identity_key"]] = it
        by_signature.setdefault(notice_key(sid, it, use_entry_id=False), []).append(it)

    # 保持期間を過ぎて片方が消えても、URLだけの説明を別告知へ再利用しない。
    urls = {}
    for it in staged.values():
        url = notice_url(it["url"])
        urls[url] = urls.get(url, 0) + 1
    shared.update(url for url, count in urls.items() if count > 1)
    return staged, archive, shared, stats


def run(sources_path: Path, data_dir: Path = DATA, today: date | None = None) -> dict:
    cfg = load_json(sources_path, {})
    rules = load_json(ROOT / "config" / "rules.json", {})
    override = json.loads(os.environ.get("SHIEN_SOURCE_OVERRIDE", "{}"))
    sources = [{**s, **override.get(s["id"], {})} for s in cfg["sources"]]
    items_path = data_dir / "items.json"
    health_path = data_dir / "health.json"
    store = load_json(items_path, {"items": []})
    health = load_json(health_path, {"sources": {}})
    if store.get("schema_version", 1) > 2:
        raise ValueError("このスクリプトより新しいデータ形式です。上書きしません")
    by_id = {it["id"]: it for it in store["items"]}
    if len(by_id) != len(store["items"]):
        raise ValueError("保存データに重複IDがあります。上書きしません")
    legacy_items = store.get("legacy_items", [])
    shared_urls = {notice_url(u) for u in store.get("shared_urls", [])}
    now = now_jst()
    stamp = now.isoformat(timespec="seconds")
    today = today or now.date()
    summary = {"new": 0, "sources": {}}

    for src in sources:
        h = health["sources"].setdefault(src["id"], {"name": src["name"], "failures": 0})
        h["name"] = src["name"]
        h["url"] = src["url"]
        try:
            got = collect(src)
            if not got:
                # 0件は「ページの作りが変わって読めなくなった」可能性が高いので失敗として扱う
                raise RuntimeError("1件も読み取れませんでした（ページの作りが変わった可能性）")
            by_id, legacy_items, shared_urls, stats = merge_source(
                by_id, legacy_items, shared_urls, src, got, rules, stamp)
            h.update({"failures": 0, "last_ok": now.isoformat(timespec="seconds"), "last_count": len(got), "last_error": None})
            summary["sources"][src["id"]] = {"ok": True, "count": len(got), **stats}
            summary["new"] += stats["new"]
        except Exception as e:  # noqa: BLE001 情報源ごとに失敗を記録して続ける
            h["failures"] = h.get("failures", 0) + 1
            h["last_error"] = str(e)[:300]
            h["last_fail"] = now.isoformat(timespec="seconds")
            summary["sources"][src["id"]] = {"ok": False, "error": str(e)[:300]}
        time.sleep(float(os.environ.get("SHIEN_SLEEP", "2")))

    # 規則（rules.json）を変えたときに、もう一覧から消えた古い記事にも新しい規則を当てる
    srcs = {s["id"]: s for s in sources}
    for it in by_id.values():
        # --sourcesで一部だけ試すとき、設定のない情報源の既定分野を消さない。
        if it.get("source") in srcs:
            it.update(classify(it["title"], rules, it.get("category", ""), srcs[it["source"]].get("default_topics", [])))

    # 古いものを捨てる（掲載日がない場合は最初に見た日で判断）
    limit = today - timedelta(days=rules.get("max_age_days", 120))
    kept = []
    for it in by_id.values():
        d = it.get("published") or it["first_seen"][:10]
        if d >= limit.isoformat():
            kept.append(it)
    kept.sort(key=lambda x: (x.get("published") or x["first_seen"][:10], x["first_seen"], x["id"]), reverse=True)
    store = {**store, "schema_version": 2, "updated": stamp, "items": kept,
             "legacy_items": legacy_items, "shared_urls": sorted(shared_urls)}
    health["checked"] = now.isoformat(timespec="seconds")
    save_json(items_path, store)
    save_json(health_path, health)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=str(ROOT / "config" / "sources.json"))
    ap.add_argument("--data-dir", default=str(DATA), help="移行を試すときはdataのコピーを指定")
    args = ap.parse_args()
    s = run(Path(args.sources), Path(args.data_dir))
    print(json.dumps(s, ensure_ascii=False, indent=1))
    # すべての情報源が失敗したときだけ異常終了にする（一部の失敗はサイトに表示して続ける）
    if s["sources"] and not any(v["ok"] for v in s["sources"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
