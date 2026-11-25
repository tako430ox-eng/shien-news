"""出典の変更を順序・個数・本文の範囲を保って見張る（標準ライブラリのみ）。

状態にはブロック/見出しのハッシュと件数だけを保存し、旧本文は保存しない。
引用は今回取得した追加ブロックの最大15件・各120文字。異常時は正常な基準を保つ。
使い方: python scripts/watch.py [--report watch_report.md]
確認済みの構造・設定変更を受け入れる: --accept-baseline URL（繰り返し可）
終了コード0でも報告があれば既存ワークフローがIssueを作る。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import ROOT, decode, fetch, load_json, now_jst, save_json  # noqa: E402
from watch_extract import extract_html  # noqa: E402

VERSION = 2
POLICY_KEYS = ("content_selectors", "peripheral", "min_body_chars", "required_text", "format")


def h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(value) -> str:
    return h(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def capture(body: bytes, headers: dict, url: str, page: dict, ignore: list[str]) -> tuple[dict, list[dict]]:
    """永続化可能なスナップショットと、今回だけの引用用ブロックを返す。"""
    ctype = next((str(v).lower().split(";")[0].strip() for k, v in headers.items()
                  if k.lower() == "content-type"), "")
    expected_pdf = urlsplit(url).path.lower().endswith(".pdf") or page.get("format") == "pdf"
    pdf_magic = bool(re.match(rb"^(?:\xef\xbb\xbf)?\s*%PDF-\d\.\d", body[:1024]))
    policy = fingerprint({"version": VERSION, "page": {k: page[k] for k in POLICY_KEYS if k in page},
                          "ignore": ignore})
    common = {"version": VERSION, "policy": policy}
    if expected_pdf or "pdf" in ctype or pdf_magic:
        if not pdf_magic or b"%%EOF" not in body[-2048:]:
            raise ValueError("PDFの署名/終端を確認できません。HTMLのエラー応答や途中切れを確認してください。")
        if ctype and ctype not in ("application/pdf", "application/octet-stream", "binary/octet-stream"):
            raise ValueError("PDFとContent-Typeが一致しません。")
        return {**common, "kind": "pdf", "digest": hashlib.sha256(body).hexdigest(),
                "layout": "pdf-bytes", "blocks": [], "body_chars": 0, "body_blocks": 0}, []
    if page.get("format") not in (None, "html"):
        raise ValueError("formatはhtmlまたはpdfを指定してください。")
    if ctype and ctype not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise ValueError("HTMLとして未対応のContent-Typeです。")
    text = decode(body, headers)
    if "\ufffd" in text or not re.search(r"<(?:!doctype|html|head|body|main|div|section|article|p|h[1-6]|table|a|ul|ol|dl)\b", text, re.I):
        raise ValueError("HTML本文を確認できません。文字化け・空応答・エラー画面を確認してください。")
    parsed = extract_html(text, url, page, ignore)
    blocks = parsed["blocks"]
    # 見出しの原文も残さない。削除位置と現在もある見出しを照合する鍵だけ持つ。
    records = [{"hash": b["hash"], "section": h(b["section"]), "zone": b["zone"], "noise": b["noise"]}
               for b in blocks]
    return {**common, "kind": "html", "digest": fingerprint(records), "blocks": records,
            "layout": parsed["layout"], "body_chars": parsed["body_chars"],
            "body_blocks": parsed["body_blocks"]}, blocks


def block_key(block: dict) -> tuple[str, str, str]:
    return block["zone"], block["section"], block["hash"]


def difference(before: list[dict], after: list[dict]) -> dict:
    """集合ではなく個数つきの比較。順序だけの変更も別に返す。"""
    old = [block_key(b) for b in before]
    new = [block_key(b) for b in after]
    added, removed = Counter(new) - Counter(old), Counter(old) - Counter(new)
    a, r = sum(added.values()), sum(removed.values())
    kind = ("modified" if a and r else "added" if a else "removed" if r else
            "reordered" if old != new else "unchanged")
    return {"change": kind, "added_count": a, "removed": r,
            "added_keys": added, "removed_keys": removed}


def describe_change(old: dict, new: dict, blocks: list[dict]) -> dict | None:
    if new["kind"] == "pdf":
        if old["digest"] == new["digest"]:
            return None
        return {"type": "changed", "change": "pdf_binary", "added": [], "added_count": 0, "removed": 0}
    before = [b for b in old["blocks"] if not b["noise"]]
    after = [b for b in new["blocks"] if not b["noise"]]
    delta = difference(before, after)
    if delta["change"] == "unchanged":
        return None
    added_keys, removed_keys = delta.pop("added_keys"), delta.pop("removed_keys")
    added, removed_details = [], []
    sections = {h(b["section"]): b["section"] for b in blocks if b["section"]}
    zones = set()
    for saved, current in zip(new["blocks"], blocks):
        key = block_key(saved)
        if not saved["noise"] and added_keys[key]:
            zones.add(saved["zone"])
            if len(added) < 15:
                added.append(current["text"][:120])
            added_keys[key] -= 1
    # 同じ文の重複削除でも個数を失わない。本文は復元できないので位置を示す。
    for position, saved in enumerate(old["blocks"], 1):
        key = block_key(saved)
        if not saved["noise"] and removed_keys[key]:
            zones.add(saved["zone"])
            if len(removed_details) < 15:
                removed_details.append({"position": position,
                                        "section": sections.get(saved["section"], "（旧見出しは復元できません）")[:120],
                                        "hash": saved["hash"][:12]})
            removed_keys[key] -= 1
    if delta["change"] == "reordered":
        zones.update(b["zone"] for b in after)
    return {"type": "changed", **delta, "added": added, "removed_details": removed_details,
            "zones": sorted(zones)}


def review_reason(old: dict, new: dict) -> str | None:
    if not isinstance(old, dict):
        return "invalid_state"
    if old.get("version") != VERSION:
        return "unsupported_state"
    if not all(k in old for k in ("kind", "policy", "layout", "digest", "blocks", "body_chars", "body_blocks")):
        return "invalid_state"
    if (old["kind"] not in ("html", "pdf") or not isinstance(old["blocks"], list)
            or not isinstance(old["body_chars"], int) or old["body_chars"] < 0):
        return "invalid_state"
    for block in old["blocks"]:
        if (not isinstance(block, dict) or block.get("zone") not in ("body", "outer", "peripheral")
                or not isinstance(block.get("noise"), bool)
                or not all(isinstance(block.get(k), str) and re.fullmatch(r"[a-f0-9]{64}", block[k]) for k in ("hash", "section"))):
            return "invalid_state"
    if old["kind"] != new["kind"]:
        return "format_changed"
    if old["policy"] != new["policy"]:
        return "policy_changed"
    if old["layout"] != new["layout"]:
        return "layout_changed"
    if old["kind"] == "html" and old["body_chars"] >= 200 and new["body_chars"] < old["body_chars"] * 0.35:
        return "body_shrunk"
    return None


def run(cfg_path: Path, state_path: Path, report_path: Path, *, accept_baselines=()) -> list[dict]:
    cfg = load_json(cfg_path, {"pages": []})
    state = load_json(state_path, {"pages": {}})
    now = now_jst().isoformat(timespec="seconds")
    changes = []
    # 新しい実装の状態を古いプログラムで上書きしない。
    if state.get("schema_version", VERSION) != VERSION:
        changes.append({"type": "review", "reason": "unsupported_state", "url": "", "what": "監視状態全体"})
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render(changes, cfg), encoding="utf-8")
        return changes
    accepted = set(accept_baselines)
    if accepted - {page["url"] for page in cfg["pages"]}:
        raise ValueError("--accept-baseline には設定済みのURLを正確に指定してください。")
    for p in cfg["pages"]:
        url = p["url"]
        st = state["pages"].setdefault(url, {})
        base = {"url": url, "what": p["what"]}
        st["checked"] = now
        try:
            body, headers = fetch(url, retries=1)
        except Exception as e:  # noqa: BLE001
            st.setdefault("unreachable_since", now)
            st["error"] = str(e)[:200]
            changes.append({**base, "type": "unreachable", "error": st["error"], "since": st["unreachable_since"]})
        else:
            # HTTP到達と内容の健全性は別。到達が戻れば以前の通信エラーを消す。
            st.pop("unreachable_since", None)
            st.pop("error", None)
            try:
                new, blocks = capture(body, headers, url, p, cfg.get("ignore", []))
            except Exception as e:  # noqa: BLE001
                st["inspection_error"] = str(e)[:200]
                changes.append({**base, "type": "inspection_error", "error": st["inspection_error"]})
            else:
                old = st.get("snapshot")
                reason = review_reason(old, new) if "snapshot" in st else None
                if reason in ("unsupported_state", "invalid_state") or (reason and url not in accepted):
                    st["inspection_error"] = reason
                    changes.append({**base, "type": "review", "reason": reason})
                else:
                    st.pop("inspection_error", None)
                    change = None
                    if reason or url in accepted:
                        change = {"type": "review", "reason": "baseline_accepted"}
                    elif old:
                        change = describe_change(old, new, blocks)
                        if not change and old["digest"] != new["digest"]:
                            delta = difference(old["blocks"], new["blocks"])
                            st["last_suppressed"] = {"checked": now, "change": delta["change"],
                                                     "added_count": delta["added_count"], "removed": delta["removed"]}
                    elif st.get("digest"):
                        # 旧PDFは同じバイトハッシュで比較可能。旧HTMLは構造/リンクの記録がなく比較不能。
                        if new["kind"] == "pdf" and not st.get("lines"):
                            change = describe_change({"digest": st["digest"]}, new, blocks)
                        else:
                            change = {"type": "review", "reason": "baseline_migration"}
                    if change:
                        changes.append({**base, **change})
                        st["changed_at"] = now
                    st.update({"snapshot": new, "first_seen": st.get("first_seen", now)})
                    st.pop("digest", None)
                    st.pop("lines", None)
        time.sleep(float(os.environ.get("SHIEN_SLEEP", "2")))
    state.update({"schema_version": VERSION, "checked": now})
    save_json(state_path, state)
    if changes:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render(changes, cfg), encoding="utf-8")
    elif report_path.exists():
        report_path.unlink()
    return changes


REASONS = {
    "baseline_migration": "旧ハッシュから新しい比較方式へ移行しました。旧方式では見出し・リンク先・本文範囲を記録していないため、今回の実ページの変更を完全に復元できません。今回は公式ページ全体を確認してください。次回から新基準で比較します。",
    "policy_changed": "抽出設定が変わりました。変更前後の範囲を確認するまで、前の正常な基準を保持します。",
    "layout_changed": "本文/周辺欄の構造が変わりました。前の正常な基準を保持します。",
    "body_shrunk": "本文が前回の35%未満になりました。大量削除または取得不調として確認が必要です。前の正常な基準を保持します。",
    "format_changed": "HTML/PDFの形式が変わりました。ページの移動やエラー応答を確認してください。前の正常な基準を保持します。",
    "unsupported_state": "この実装では読めない版の監視状態です。基準を上書きせず、実装の版と状態ファイルを確認してください。",
    "invalid_state": "監視状態の形式が壊れています。基準を上書きせず、状態ファイルを確認してください。",
    "baseline_accepted": "指定されたURLの基準を、今回の正常な取得結果で更新しました。",
}


def quote(text: str, limit: int = 120) -> str:
    # 外部ページの文字でMarkdown/HTMLやメンションを作らない。
    text = html.escape(" ".join(text.split())[:limit], quote=False).replace("@", "＠")
    return re.sub(r"([\\\x60*_{}\[\]()#!|>])", r"\\\1", text)


def render(changes: list[dict], cfg: dict) -> str:
    out = ["出典の変更または監視の異常を検知しました。制度変更が確定したという意味ではありません。金額・条件・電話番号・窓口を公式ページで確認してください。", "",
           f"対象：{quote(cfg.get('used_by', ''), 400)}", ""]
    labels = {"added": "追加", "removed": "削除", "modified": "追加と削除（書き換えを含む）", "reordered": "並べ替え（順序のみ）"}
    zones = {"body": "本文", "outer": "本文外の未除外情報", "peripheral": "関連欄の未確認情報"}
    for c in changes:
        out += [f"### {quote(c['what'])}", f"- {c['url']}"]
        if c["type"] == "changed":
            if c["change"] == "pdf_binary":
                out.append("- PDFのバイト列が変わりました。本文・画像・メタデータのどれが変わったかは解析していません。PDFを開いて確認してください。")
            else:
                out.append(f"- {labels[c['change']]}：追加 {c['added_count']} ブロック、削除 {c['removed']} ブロック。")
                if c.get("zones"):
                    out.append("- 範囲：" + "、".join(zones[z] for z in c["zones"]))
                if c["change"] == "reordered":
                    out.append("- ブロックの内容と個数は同じです。順番による条件・金額の対応の変化があり得るため通知しています。")
                if c["added"]:
                    out.append("- 今回の追加ブロック（最大15件、各120文字。比較用に表記を正規化）：")
                    out += [f"  - {quote(line)}" for line in c["added"]]
                if c["removed"]:
                    out.append("- 旧本文は保存していないため、削除された文そのものは復元できません。以下は削除候補の旧位置と照合できた現在の見出しです（最大15件。同一ブロックが重複する場合、位置は一意に決まりません）。")
                    out += [f"  - 旧位置 {d['position']}、見出し {quote(d['section'])}、照合ID {d['hash']}" for d in c["removed_details"]]
        elif c["type"] == "unreachable":
            out += [f"- 開けませんでした（{c['since']} から）：{quote(c['error'], 200)}",
                    "- 最後の正常な基準を保持しています。継続する場合は移動・削除を確認してください。"]
        elif c["type"] == "inspection_error":
            out += [f"- 取得内容を安全に比較できません：{quote(c['error'], 200)}",
                    "- 最後の正常な基準を保持しています。本文が空・範囲が不明な状態を無変化とは扱いません。"]
        else:
            out.append("- " + REASONS[c["reason"]])
        out.append("")
    out += ["本文外も原則として監視します。設定で範囲・リンク先・見出しを確認した周辺リンクだけを通知から除外します。",
            "リンク先URLの変更は検知します。同じURLのPDFの中身を見張るには、そのPDF自体をconfig/watch.jsonに登録してください。",
            "構造・設定の変化を確認して基準を更新するときは、docs/watch-design.md の --accept-baseline の手順を使ってください。",
            "公式ページと照合して必要な文面や確認日を更新した後、Issueを閉じてください。"]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config" / "watch.json"))
    ap.add_argument("--state", default=str(ROOT / "data" / "watch_state.json"))
    ap.add_argument("--report", default=str(ROOT / "watch_report.md"))
    ap.add_argument("--accept-baseline", action="append", default=[], metavar="URL",
                    help="公式ページを確認済みのURLだけ、新しい正常な基準を明示的に受け入れる")
    a = ap.parse_args()
    ch = run(Path(a.config), Path(a.state), Path(a.report), accept_baselines=a.accept_baseline)
    print(f"changes: {len(ch)}")


if __name__ == "__main__":
    main()
