"""出典ページの変更を見張る。変わったページ・開けなくなったページを watch_report.md に書く。

本文そのものは保存しない（各行の要約値＝ハッシュだけを保存する）。報告には新しく増えた行を
最大15行まで引用し、人が公式ページを開いて確かめるきっかけにする。

使い方: python scripts/watch.py [--report watch_report.md]
終了コード: 0=変化なし, 0 でも watch_report.md があれば変化あり（ワークフローが Issue を作る）
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import ROOT, _Walker, clean, decode, fetch, load_json, now_jst, save_json  # noqa: E402


def page_lines(body: bytes, headers: dict, url: str, ignore: list[str]) -> tuple[str, list[str]]:
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return hashlib.sha256(body).hexdigest(), []
    w = _Walker()
    w.feed(decode(body, headers))
    lines = []
    pats = [re.compile(p) for p in ignore]
    for t in w.tokens:
        s = clean(t["text"])
        if len(s) < 2 or any(p.search(s) for p in pats):
            continue
        lines.append(s)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest, lines


def h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def run(cfg_path: Path, state_path: Path, report_path: Path) -> list[dict]:
    cfg = load_json(cfg_path, {"pages": []})
    state = load_json(state_path, {"pages": {}})
    now = now_jst().isoformat(timespec="seconds")
    changes = []
    for p in cfg["pages"]:
        url = p["url"]
        st = state["pages"].setdefault(url, {})
        try:
            body, headers = fetch(url, retries=1)
            digest, lines = page_lines(body, headers, url, cfg.get("ignore", []))
            if st.get("unreachable_since"):
                st.pop("unreachable_since", None)
            if st.get("digest") and st["digest"] != digest:
                old = set(st.get("lines", []))
                added = [l for l in lines if h(l) not in old][:15]
                removed = len(old - {h(l) for l in lines}) if old else None
                changes.append({"url": url, "what": p["what"], "type": "changed", "added": added, "removed": removed})
                st["changed_at"] = now
            st.update({"digest": digest, "lines": [h(l) for l in lines], "checked": now, "first_seen": st.get("first_seen", now)})
        except Exception as e:  # noqa: BLE001
            st.setdefault("unreachable_since", now)
            st["error"] = str(e)[:200]
            changes.append({"url": url, "what": p["what"], "type": "unreachable", "error": str(e)[:200], "since": st["unreachable_since"]})
        time.sleep(float(os.environ.get("SHIEN_SLEEP", "2")))
    state["checked"] = now
    save_json(state_path, state)
    if changes:
        report_path.write_text(render(changes, cfg), encoding="utf-8")
    elif report_path.exists():
        report_path.unlink()
    return changes


def render(changes: list[dict], cfg: dict) -> str:
    out = ["行動支援の試作が出典にしている公式ページに、変化がありました。試作の文面（金額・条件・電話番号・窓口名など）が今も正しいか、公式ページを開いて確かめてください。", "",
           f"対象の試作：`{cfg.get('used_by', '')}`", ""]
    for c in changes:
        if c["type"] == "changed":
            out.append(f"### 内容が変わった：{c['what']}")
            out.append(f"- {c['url']}")
            if c["added"]:
                out.append("- 新しく出てきた文（最大15行。公式ページからの引用）：")
                out += [f"  - {l[:120]}" for l in c["added"]]
            else:
                out.append("- 文の追加はなく、削除または並べ替えだけの可能性があります（PDFは中身を比べていません）。")
        else:
            out.append(f"### 開けなかった：{c['what']}")
            out.append(f"- {c['url']}（{c['since']} から。理由：{c['error']}）")
            out.append("- 一時的な不調のこともあります。2回続いたら、ページの移動・削除を疑ってください。")
        out.append("")
    out.append("確かめたら、試作と設計書の確認日を更新して、この Issue を閉じてください。")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config" / "watch.json"))
    ap.add_argument("--state", default=str(ROOT / "data" / "watch_state.json"))
    ap.add_argument("--report", default=str(ROOT / "watch_report.md"))
    a = ap.parse_args()
    ch = run(Path(a.config), Path(a.state), Path(a.report))
    print(f"changes: {len(ch)}")


if __name__ == "__main__":
    main()
