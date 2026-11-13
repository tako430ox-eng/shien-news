"""表示確認用の見本データを作る。見出しとURLは、2026年11月13日に各機関の新着一覧で実際に確認できたもの（調査で読み取った範囲）。"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lib
rules = json.loads((ROOT / "config/rules.json").read_text(encoding="utf-8"))
REAL = [
 ("mhlw", "厚生労働省", "上野厚生労働大臣　閣議後記者会見のお知らせ", "https://www.mhlw.go.jp/stf/newpage_77378.html", "2026-11-12", "報道発表"),
 ("mhlw", "厚生労働省", "社会保障審議会（介護保険部会）", "https://www.mhlw.go.jp/stf/shingi/shingi-hosho_126734.html", "2026-11-12", "審議会等"),
 ("mhlw", "厚生労働省", "令和８年度「職業能力開発関係厚生労働大臣表彰」の受賞者が決定", "https://www.mhlw.go.jp/stf/newpage_77259.html", "2026-11-12", "報道発表"),
 ("cfa", "こども家庭庁", "第5回ひとり親家庭支援ワーキンググループの開催について掲載しました", "https://www.cfa.go.jp/press/dd446ba4-b3cb-40be-b893-ac3eb79b5edf", "2026-11-10", "報道発表"),
 ("cfa", "こども家庭庁", "第8回 こどものケアニーズに応じた支援の在り方に関する専門委員会の開催について掲載しました", "https://www.cfa.go.jp/press/944d3758-23fc-427e-8c38-120cc81d84be", "2026-11-10", "報道発表"),
 ("cfa", "こども家庭庁", "令和8年度「こども・若者育成支援研修～地域における多様なアプローチを学ぶ～」に係る研修生募集要項を掲載しました", "https://www.cfa.go.jp/policies/youth/ikusei-hikikomori/youkou-r8", "2026-11-10", "政策"),
 ("isa", "出入国在留管理庁", "生活オリエンテーション動画（ショート版）を配信しています！", "https://www.moj.go.jp/isa/support/coexistence/04_00125.html", "2026-11-10", "在留支援"),
 ("isa", "出入国在留管理庁", "最新のウクライナ避難民に関する情報を更新しました。", "https://www.moj.go.jp/isa/support/fresc/01_00234.html", "2026-11-04", "在留支援"),
 ("isa", "出入国在留管理庁", "特定技能１号（外食業分野）の在留諸申請の審査状況について更新しました。", "https://www.moj.go.jp/isa/10_00259.html", "2026-11-10", "制度・手続案内"),
 ("digital", "デジタル庁", "預貯金者の意思に基づく個人番号の利用による預貯金口座の管理等に関する法律施行規則の一部を改正する命令案に係る意見募集を行います", "https://www.digital.go.jp/get-involved/procedure/98e55a91-a46b-4f92-ac1f-7079d9d43c15", "2026-11-12", "意見募集"),
 ("digital", "デジタル庁", "広報資料に「スマートフォンのマイナンバーカード」に関するポスターを掲載しました", "https://www.digital.go.jp/policies/mynumber_resources", "2026-11-12", ""),
]
items = []
for sid, name, title, url, d, cat in REAL:
    c = lib.classify(title, rules, cat, {"cfa": ["children"], "isa": ["foreign"]}.get(sid, []))
    items.append({"id": lib.item_id(url), "source": sid, "source_name": name, "title": title, "url": url, "published": d, "category": cat,
                  "first_seen": d + "T07:17:00+09:00", "last_seen": "2026-11-13T07:17:00+09:00", "publish": "auto", **c})
items.sort(key=lambda x: x["published"], reverse=True)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
now = lib.now_jst().isoformat(timespec="seconds")
(out / "items.json").write_text(json.dumps({"updated": now, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
(out / "health.json").write_text(json.dumps({"sources": {s: {"name": n, "failures": 0, "last_ok": now} for s, n in [("mhlw","厚生労働省"),("cfa","こども家庭庁"),("isa","出入国在留管理庁"),("digital","デジタル庁")]}}, ensure_ascii=False), encoding="utf-8")
for f in ("notes.json", "deadlines.json"):
    (out / f).write_text((ROOT / "data" / f).read_text(encoding="utf-8"), encoding="utf-8")
for it in items:
    print(("表示" if (not it["excluded"] and it["topics"]) else "除外"), it["kind"], it["topics"], it["title"][:40])
