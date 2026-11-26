"""data/ の内容から静的なサイト（site/）を作る。JavaScriptがなくても読めるHTMLを出す。

使い方: python scripts/build.py [--out site] [--sample]
--sample は表示確認用。画面の上に「見本のデータ」と大きく出す。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import JST, ROOT, load_json, notice_url, now_jst, save_json  # noqa: E402

WD = "月火水木金土日"
PLACEHOLDER = "【未記入"
# Search Console の所有確認。meta タグの content の中身と、HTML ファイルの名前の形
VERIFY_TOKEN = re.compile(r"[A-Za-z0-9_-]{10,100}")
VERIFY_FILE = re.compile(r"google[0-9a-f]{8,32}\.html")
# 感想のアンケート。pages/ のHTMLに置いた目印を、設定があれば案内に、なければ空に置き換える
SURVEY_MARK = re.compile(r"<!--survey:([a-z]+)-->")
SURVEY_URL = re.compile(r"https://[A-Za-z0-9.-]+/[^\s\"'<>]*")
SURVEY_ENTRY = re.compile(r"entry\.[0-9]{3,20}")
# 家賃編の「最初に出る画面」を公開する HTML に書き込むスクリプト（node で動かす）。理由はスクリプトの先頭に書いた
PRERENDER = ROOT / "scripts" / "prerender_yachin.cjs"


# ページの中から外のサーバーへ読み込み・送信をさせない決まり（Content-Security-Policy）。
# 「ページを開いても外に接続しない」「入力を送らない」という約束を、ブラウザの側でも守らせる二重の備え。
# リンクを押して公式ページや Google 検索・アンケートへ移ることは妨げない（移動はこの決まりの対象外）。
# GitHub Pages は応答ヘッダーを変えられないので meta で書く（frame-ancestors は meta では効かない）。
# スクリプトは、このサイトのファイル（'self'）と、ページの中に書いたスクリプトのうち公開時にハッシュを計算したものだけを動かす。
# 'unsafe-inline' は使わない（2026-11-26 外部レビュー 10：万一ページに文字が差し込まれても、スクリプトとしては動かないように）。
# style はページの中の style 属性を使っているので 'unsafe-inline' を許す（スクリプトは動かせない）。
CSP_REST = ("style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'")


def csp(hashes: tuple[str, ...] = ()) -> str:
    src = " ".join(["'self'"] + [f"'sha256-{h}'" for h in hashes])
    return f"default-src 'self'; script-src {src}; {CSP_REST}"


CSP = csp()
CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
CSP_META_RE = re.compile(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">')
CHARSET_META = '<meta charset="utf-8">'
INLINE_SCRIPT = re.compile(r"<script>([\s\S]*?)</script>")


def script_hashes(text: str) -> tuple[str, ...]:
    """ページの中に書いたスクリプト（src のない <script>）の中身の SHA-256（ブラウザが比べるのと同じ、UTF-8 の文字列そのまま）。"""
    return tuple(base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii")
                 for m in INLINE_SCRIPT.finditer(text))


def add_csp(text: str) -> str:
    """pages/ から写す HTML に CSP の meta を足す（文字コードの meta のすぐ後ろ。スクリプトより前）。
    ページの中のスクリプトは、ここで計算したハッシュで許す。スクリプトを書き換えたあと（メニュー・アンケートの差し込み、
    家賃編の書き込みのあと）に呼ぶこと。"""
    if CSP_META_RE.search(text) or CHARSET_META not in text:
        return text
    meta = f'<meta http-equiv="Content-Security-Policy" content="{csp(script_hashes(text))}">'
    return text.replace(CHARSET_META, CHARSET_META + "\n" + meta, 1)


MENU_MARK = "<!--site-menu-->"
# サイトのメニュー（看板の右上の「メニュー」ボタンで開く一覧）。全ページで同じ並び。
# 行き先はサイトの一番上からの道筋（"" はトップ）。新しいページを足したら、ここにも足す。
SITE_MENU = [
    ("困っているとき", [
        ("yachin/", "家賃が払えない・滞納したとき"),
        ("shitsugyo/", "仕事を失った・辞めたとき"),
        ("sasaeru/", "家族や友人が生活に困っているとき（支える人へ）"),
        ("#help", "困ったときの相談先"),
    ]),
    ("新着とこのサイト", [
        ("", "支援の新着（トップ）"),
        ("talk.html", "国で話し合っていること"),
        ("about.html", "新着のしくみと出典"),
        ("unei.html", "運営者と連絡先"),
    ]),
]
MENU_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
             '<path class="i-open" d="M4 6.5h16M4 12h16M4 17.5h16"/><path class="i-close" d="M6 6l12 12M18 6L6 18"/></svg>')


def menu_html(depth: int, current: str) -> str:
    """看板の右上に置くメニューボタンと、開くページの一覧。current は今のページの道筋（例 "sasaeru/denwa/"）。

    ボタンは hidden で出し、assets/menu.js が動いたときだけ見せる。スクリプトがないときは
    ページの下の案内から移れる（今までどおり）。外部への通信はしない。
    """
    up = "../" * depth
    current = current[: -len("index.html")] if current.endswith("index.html") else current
    groups = []
    for title, items in SITE_MENU:
        rows = []
        for path, label in items:
            if path == "#help":
                href = "#help" if current == "" else up + "index.html#help"
            else:
                href = up + path or "./"
            mark = ""
            if path == current:
                mark = ' aria-current="page"'
            elif path and path.endswith("/") and current.startswith(path):
                mark = ' aria-current="true"'
            rows.append(f'<li><a href="{e(href)}"{mark}>{e(label)}</a></li>')
        groups.append(f'<div><p class="site-menu-h">{e(title)}</p><ul>{"".join(rows)}</ul></div>')
    return (f'<button type="button" class="menu-btn" data-site-menu aria-expanded="false" aria-controls="site-menu" hidden>'
            f'{MENU_ICON}<span class="menu-label">メニュー</span></button>'
            f'<nav class="site-menu" id="site-menu" aria-label="サイトのメニュー" hidden>{"".join(groups)}</nav>'
            f'<script src="{up}assets/menu.js" defer></script>')


def prerender_yachin(text: str) -> str:
    """家賃編の本文（今日やること・手順・制度の候補）を、プログラムなしでも読めるように書き込む。

    node がない、または失敗したときは、元の HTML をそのまま返す（公開は止めない）。
    """
    node = shutil.which("node")
    if not node:
        print("家賃編の本文の書き込みを飛ばしました：node が見つかりません", file=sys.stderr)
        return text
    try:
        r = subprocess.run([node, str(PRERENDER)], input=text, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as ex:
        print(f"家賃編の本文の書き込みを飛ばしました：{ex}", file=sys.stderr)
        return text
    if r.returncode != 0 or 'id="say_first_call"' not in r.stdout:
        print("家賃編の本文の書き込みを飛ばしました：" + (r.stderr.strip().splitlines() or ["結果が空です"])[-1], file=sys.stderr)
        return text
    return r.stdout


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def jdate(iso: str | None, with_year=False) -> str:
    if not iso:
        return "日付なし"
    d = date.fromisoformat(iso[:10])
    s = f"{d.month}月{d.day}日（{WD[d.weekday()]}）"
    return f"{d.year}年{s}" if with_year else s


def safe_link(url) -> bool:
    """リンクに出してよいURLか（http・https だけ）。取得側でも捨てているが、保存データが古い・壊れたときの二重の備え。"""
    return isinstance(url, str) and urlsplit(url.strip()).scheme.lower() in ("http", "https")


def visible(it: dict) -> bool:
    return it.get("publish") == "auto" and not it.get("excluded") and bool(it.get("topics")) and safe_link(it.get("url"))


def resolve_notes(store: dict, notes: list[dict]) -> dict[str, dict]:
    """説明を告知IDに結ぶ。共有URL・旧混在行への曖昧な説明は公開しない。"""
    items_by_id = defaultdict(list)
    items_by_url = defaultdict(list)
    for it in store["items"]:  # 非表示の告知も数える。表示対象だけでは一意とは限らない。
        items_by_id[it["id"]].append(it)
        items_by_url[notice_url(it["url"])].append(it)
    shared = {notice_url(url) for url in store.get("shared_urls", [])}
    shared.update(notice_url(it["url"]) for it in store.get("legacy_items", []))
    explicit = defaultdict(list)
    legacy = defaultdict(list)
    invalid_urls = set()
    for n in notes:
        url = notice_url(n["url"]) if isinstance(n.get("url"), str) else ""
        if "item_id" in n:
            iid = n["item_id"]
            if not isinstance(iid, str) or not iid:
                invalid_urls.add(url)
                print("やさしい説明を保留：item_id が空か文字列ではありません", file=sys.stderr)
                continue
            explicit[iid].append(n)
        else:
            legacy[url].append(n)

    resolved = {}
    for iid, group in explicit.items():
        candidates = items_by_id.get(iid, [])
        if len(group) != 1 or len(candidates) != 1 or group[0].get("url") != candidates[0]["url"]:
            invalid_urls.update(notice_url(n["url"]) for n in group if isinstance(n.get("url"), str))
            print(f"やさしい説明を保留：item_id={iid} の指定が重複・不明、または URL が一致しません", file=sys.stderr)
            continue
        resolved[iid] = group[0]
    for url, group in legacy.items():
        candidates = items_by_url.get(url, [])
        if not url or len(group) != 1 or len(candidates) != 1 or url in shared or url in invalid_urls:
            print(f"やさしい説明を保留：URL だけでは対象を一意に決められません：{url}", file=sys.stderr)
            continue
        iid = candidates[0]["id"]
        if len(items_by_id[iid]) != 1:
            print(f"やさしい説明を保留：告知IDが重複しています：{iid}", file=sys.stderr)
            continue
        # ID指定に誤り・重複がある場合も、古いURL指定へ戻して隠さない。
        if iid not in explicit:
            resolved[iid] = group[0]
    return resolved


class Site:
    def __init__(self, data_dir: Path, sample: bool = False):
        self.cfg = load_json(ROOT / "config" / "site.json", {})
        self.rules = load_json(ROOT / "config" / "rules.json", {})
        self.sources = {s["id"]: s for s in load_json(ROOT / "config" / "sources.json", {"sources": []})["sources"]}
        self.store = load_json(data_dir / "items.json", {"items": [], "updated": None})
        self.health = load_json(data_dir / "health.json", {"sources": {}})
        self.notes = resolve_notes(self.store, load_json(data_dir / "notes.json", {"notes": []})["notes"])
        self.deadlines = load_json(data_dir / "deadlines.json", {"deadlines": []})["deadlines"]
        self.sample = sample
        self.topics = {t["slug"]: t for t in self.rules["topics"]}
        self.kinds = {k["id"]: k for k in self.rules["kinds"]}
        self.kinds[self.rules["default_kind"]["id"]] = self.rules["default_kind"]
        self.built = now_jst()
        # 公式資料で対象期間を確認した記事だけ、最終日の翌日（日本時間）から掲載を終える。
        expired_urls = {
            url for url, period in self.rules.get("reviewed_notice_periods", {}).items()
            if date.fromisoformat(period["last_day"]) < self.built.date()
        }
        self.items = [it for it in self.store["items"] if visible(it) and it["url"] not in expired_urls]
        talk_kinds = {k["id"] for k in self.rules["kinds"] if k.get("group") == "talk"}
        useful_kinds = set(self.rules.get("useful_kinds", []))
        # 3つに分ける。暮らしに役立つお知らせ（制度の変更・募集/申請・相談窓口・注意）、
        # 国で話し合っていること（会議・意見募集・調査）、そのほかのお知らせ（行事・広報など）
        # useful_kinds を書かないときは、話し合い以外をすべて役立つ欄に出す（そのほかは空）
        # group が other の種類（行事・広報。2026-11-21 コタローさんの判断）は、いつも「そのほかのお知らせ」に出す
        other_kinds = {k["id"] for k in self.rules["kinds"] if k.get("group") == "other"}
        useful_kinds = set(self.rules.get("useful_kinds") or {it["kind"] for it in self.items} - talk_kinds - other_kinds)
        self.useful = [it for it in self.items if it["kind"] in useful_kinds]
        self.talk = [it for it in self.items if it["kind"] in talk_kinds]
        self.other = [it for it in self.items if it["kind"] not in useful_kinds and it["kind"] not in talk_kinds]
        oks = [h.get("last_ok") for h in self.health.get("sources", {}).values() if h.get("last_ok")]
        # 「最後に情報源から実際に取れた時刻」。ページを作り直しただけでは新しくならない
        self.fresh = max(oks) if oks else ""

    # ------------------------------------------------------------ 部品
    def url(self, path: str) -> str:
        """公開URL。トップは index.html を付けない形（人がリンクやブックマークに使う形）に揃える。"""
        if path == "index.html" or path.endswith("/index.html"):
            path = path[: -len("index.html")]
        return urljoin(self.cfg["base_url"], path)

    @staticmethod
    def newest(items: list[dict]) -> str | None:
        """一覧に載っているお知らせのうち、いちばん新しい日付（サイトマップの lastmod に使う）。"""
        days = [(it.get("published") or it.get("first_seen") or "")[:10] for it in items]
        days = [d for d in days if d]
        return max(days) if days else None

    def head(self, title: str, desc: str, path: str, depth: int) -> str:
        up = "../" * depth
        canon = self.url(path)
        verify = self.cfg.get("google_site_verification", "")
        if verify and not VERIFY_TOKEN.fullmatch(verify):
            raise ValueError("config/site.json の google_site_verification は content=\"…\" の中身だけを書いてください")
        return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
{CSP_META}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(canon)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(canon)}">
{f'<meta name="google-site-verification" content="{e(verify)}">' + chr(10) if verify else ""}<meta name="theme-color" content="#fffaf2">
<link rel="alternate" type="application/atom+xml" title="{e(self.cfg['name'])}" href="{up}feed.xml">
<link rel="stylesheet" href="{up}assets/fonts.css">
<link rel="stylesheet" href="{up}assets/tokens.css">
<link rel="stylesheet" href="{up}assets/site.css">
<link rel="stylesheet" href="{up}assets/menu.css">
</head>
<body data-generated="{e(self.fresh)}" data-stale-hours="{e(self.cfg.get('stale_hours', 72))}">
<a class="skip" href="#main">本文へ</a>
"""

    def header(self, depth: int, current: str, hero: bool = False) -> str:
        up = "../" * depth
        sample = ""
        if self.sample:
            sample = '<div class="sample">画面の確認用の見本です。並んでいる項目は2026年11月12〜13日に各機関の新着一覧に実際に出ていた見出しの一部で、公開後は毎日の自動取得の結果に置き換わります。</div>'
        brand = e(self.cfg.get("brand", self.cfg["name"]))
        logo = '<svg class="logo" viewBox="0 0 30 30" aria-hidden="true" focusable="false"><rect x="7" y="3" width="17" height="25" rx="2" fill="var(--c-sign-accent)" stroke="var(--c-sign-ink)" stroke-width="2.5"/><path d="M7 3 L15 6 L15 30 L7 28 Z" fill="var(--c-sign-ink)"/><circle cx="13" cy="17" r="1.4" fill="var(--c-sign)"/></svg>'
        path = '<svg class="sign-path" viewBox="0 0 360 40" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path d="M0 34 C 90 34, 150 10, 330 14" stroke="var(--c-sign-accent)" stroke-width="10" fill="none" stroke-linecap="round"/><path d="M0 34 C 90 34, 150 10, 330 14" stroke="rgba(0,0,0,0.22)" stroke-width="10" fill="none" stroke-dasharray="2 6"/></svg>'
        if hero:
            top = f"""<div class="brandrow topbar">{logo}<span class="brandname"><span class="brand">{brand}</span><span class="tag">困ったときの入口</span></span>{menu_html(depth, current)}</div>
<header class="hero">
 <h1>困っていることから、<br><em>今日やること</em>を探す。</h1>
 <p class="lead">家賃、仕事、家族や友人のこと。選ぶと、どこに連絡して何と言うかが分かります。</p>
 {path}
</header>"""
        else:
            top = f"""<div class="brandrow topbar">{logo}<a class="brand" href="{up}index.html">{brand} 新着</a>{menu_html(depth, current)}</div>
<header class="hero hero-sub">
 {path}
</header>"""
        return f"""{sample}<div class="page">
{top}
<div id="stale" class="notice" role="status" hidden></div>
"""

    def footer(self, depth: int) -> str:
        up = "../" * depth
        return f"""<footer class="foot">
  <p>このページは、国の機関の公式ページに出た新着情報を、プログラムで毎日自動で集めて並べたものです。国が作ったページではありません。見出しは公式ページのままで、内容の要約や判断はしていません。申請できるか、いつまでかなどは、必ずリンク先の公式ページか窓口で確かめてください。</p>
  <p><a href="{up}about.html">このページについて（出典・しくみ・情報源の状態）</a> ・ <a href="{up}feed.xml">新着をRSSで受け取る</a></p>
{self.survey_foot(up)}  <p>情報源から最後に取得できた時刻：{e(self.updated_text())}</p>
</footer>
</div>
<script src="{up}assets/site.js" defer></script>
</body>
</html>
"""

    # ------------------------------------------------------------ 感想のアンケート
    def survey(self) -> dict:
        """config/site.json の survey。url が空なら {}（どこにもアンケートを出さない）。形が違えば止める。"""
        sv = self.cfg.get("survey") or {}
        url = sv.get("url", "")
        if not url:
            return {}
        if not SURVEY_URL.fullmatch(url):
            raise ValueError("config/site.json の survey.url は https:// で始まるアンケートのアドレスだけを書いてください")
        entry = sv.get("page_entry", "")
        if entry and not SURVEY_ENTRY.fullmatch(entry):
            raise ValueError("config/site.json の survey.page_entry は entry.数字 の形で書いてください（事前入力リンクから写す）")
        return sv

    def survey_url(self, page: str) -> str:
        """アンケートのアドレス。page_entry があれば、開いたページを最初から選んだ状態にする。"""
        sv = self.survey()
        url, entry = sv["url"], sv.get("page_entry", "")
        value = (sv.get("page_values") or {}).get(page, "")
        if entry and value:
            url += ("&" if "?" in url else "?") + "usp=pp_url&" + entry + "=" + quote(value)
        return url

    def survey_prefill(self, page: str) -> str:
        """事前入力で Google に伝わるページの種類。事前入力しないなら空。"""
        sv = self.survey()
        return (sv.get("page_values") or {}).get(page, "") if sv.get("page_entry") else ""

    def survey_note(self, up: str, page: str = "") -> str:
        sv = self.survey()
        svc = e(sv.get("service", "外部のフォーム"))
        # 事前入力の値はアドレスに入るので、送信しなくても、開いた時点でフォームの会社に伝わる（2026-11-26 外部レビュー 8）
        kind = self.survey_prefill(page)
        told = (f'開くと、このページの種類（「{e(kind)}」）がアドレスの一部として{svc}に伝わります。' if kind else "")
        return (f'押すと、{svc}が別の画面で開きます。{told}このページで選んだ内容やメモは送られません。'
                'アンケートの答えは、送信を押したときだけ運営者に届きます。名前や連絡先は聞きません。'
                f'返事はできないので、困っていることは相談先に連絡してください。<a href="{up}unei.html#kansou">アンケートについて</a>')

    def survey_block(self, page: str, depth: int) -> str:
        """pages/ のページ（家賃編など）の最後に置く案内。設定がなければ空。"""
        if not self.survey():
            return ""
        up = "../" * depth
        return f"""<section class="survey stack small" aria-labelledby="survey-h">
    <strong id="survey-h">使ってみて、どうでしたか</strong>
    <p>ページを直すために、1分ほどのアンケートに答えてもらえると助かります。答えなくても、このページはそのまま使えます。</p>
    <p><a class="survey-link" href="{e(self.survey_url(page))}" target="_blank" rel="noopener">感想のアンケートに答える（任意）</a></p>
    <p class="muted">{self.survey_note(up, page)}</p>
  </section>"""

    def survey_unei(self) -> str:
        """運営者ページに置く、アンケートで何がどこへ送られるかの説明。設定がなければ空。"""
        sv = self.survey()
        if not sv:
            return ""
        svc = e(sv.get("service", "外部のフォーム"))
        return f"""<h2 id="kansou">感想のアンケートについて</h2>
<ul>
<li>ページを直すための、任意のアンケートです。答えなくても、どのページもそのまま使えます。</li>
<li>{svc}（Google社のサービス）を使っています。アンケートを開いたときと送信したときに Google社のサーバーに接続し、答えは Google社のサーバーに保存されます。</li>
<li>運営者に届くのは、送信したアンケートの答えだけです。このサイトで選んだ内容やメモは送られません。メールアドレスは集めない設定にしています。どのページから開いたか（家賃・失業・支える人へ・新着）は、最初から選んだ状態で開きます（変えられます）。この選んだ状態はアンケートのアドレスに入っているので、送信しなくても、開いた時点で Google社に伝わります。</li>
<li>名前・住所・電話番号・メールアドレスや、相談の具体的な中身は書かないでください。書かれていた場合は、運営者が見つけしだい消します。</li>
<li>アンケートには返事ができません。困っていることは、下の相談先か、お住まいの市区町村の窓口に連絡してください。</li>
<li>答えはページを直すためだけに使います。外に出すときは、人数や割合と、だれのものか分からないように直した要点だけにします。</li>
<li>アンケートで、お金、パスワード、名前や電話番号を聞くことはありません。<a href="{e(sv['url'])}" rel="noopener">アンケートを開く</a></li>
</ul>"""

    def survey_foot(self, up: str) -> str:
        if not self.survey():
            return ""
        return f'  <p><a href="{e(self.survey_url("news"))}" target="_blank" rel="noopener">感想のアンケートに答える（任意・1分）</a><br>{self.survey_note(up, "news")}</p>\n'

    def updated_text(self) -> str:
        u = self.fresh
        if not u:
            return "まだ取得していません"
        d = datetime.fromisoformat(u).astimezone(JST)
        return f"{d.year}年{d.month}月{d.day}日 {d.hour}時{d.minute:02d}分（日本時間）"

    def source_warnings(self) -> str:
        bad = [h for h in self.health.get("sources", {}).values() if h.get("failures", 0) >= 2]
        if not bad:
            return ""
        names = "、".join(e(h["name"]) for h in bad)
        return f'<div class="notice" role="status">{names}の新着が、いま自動で取れていません。この情報源の最新の情報は、公式ページで直接確かめてください。</div>'

    def chips(self, depth: int, current: str | None) -> str:
        up = "../" * depth
        counts = {t: 0 for t in self.topics}
        for it in self.useful:
            for t in it["topics"]:
                counts[t] = counts.get(t, 0) + 1
        out = ['<nav class="chips" aria-label="分野で絞る">']
        cls = ' aria-current="page"' if current is None else ""
        out.append(f'<a class="chip" href="{up}index.html"{cls}>すべて</a>')
        for slug, t in self.topics.items():
            if not counts.get(slug) and not any(slug in it["topics"] for it in self.talk + self.other):
                continue
            cls = ' aria-current="page"' if current == slug else ""
            out.append(f'<a class="chip" href="{up}t/{slug}.html"{cls}>{e(t["label"])}<span class="n">新着{counts.get(slug, 0)}件</span></a>')
        out.append("</nav>")
        return "\n".join(out)

    def card(self, it: dict, depth: int) -> str:
        k = self.kinds.get(it["kind"], self.rules["default_kind"])
        tags = "".join(f'<li>{e(self.topics[t]["label"])}</li>' for t in it["topics"] if t in self.topics)
        extra = ""
        if it.get("business"):
            extra += '<li class="biz">事業者向けかもしれません</li>'
        note = self.notes.get(it["id"])
        note_html = ""
        if note:
            note_html = (f'<div class="plain"><p class="plain-h">やさしい説明（人が公式ページで確かめました：{e(jdate(note["checked"], True))}）</p>'
                         f'<p>{e(note["text"])}</p></div>')
        src = self.sources.get(it["source"], {})
        return f"""<li class="card kind-{e(it['kind'])}">
 <p class="meta"><span class="kind" title="{e(k['hint'])}">{e(k['label'])}</span><span class="src">{e(it['source_name'])}</span><time datetime="{e(it.get('published') or '')}">{e(jdate(it.get('published')))}</time></p>
 <h3 class="title"><a href="{e(it['url'])}" rel="noopener">{e(it['title'])}</a></h3>
 {f'<p class="hint">{e(k["hint"])}</p>' if k.get('hint') else ''}
 {note_html}
 <ul class="tags" aria-label="分野（見出しの言葉から自動で付けたもの）">{tags}{extra}</ul>
 <p class="credit">{e(src.get('credit', '出典：' + it['source_name']))}（<a href="{e(it['url'])}" rel="noopener">公式ページ</a>）</p>
</li>"""

    def talk_box(self, depth: int, items: list[dict], scope: str = "") -> str:
        """会議・意見募集・調査をまとめた囲み。最新3件と、一覧ページへのリンク。"""
        up = "../" * depth
        if not items:
            return ""
        latest = "".join(f'<li><a href="{e(it["url"])}" rel="noopener">{e(it["title"])}</a><span class="small">（{e(it["source_name"])}・{e(jdate(it.get("published")))}）</span></li>' for it in items[:3])
        return f"""<section class="box talk">
 <h2>{e(scope)}国で話し合っていること<span class="n">{len(items)}件</span></h2>
 <p class="small">審議会や検討会の開催、意見募集、調査の結果です。<b>検討段階の案と、すでに公表された調査結果などを含みます。支援を利用できるかは、公式ページで確かめてください。</b>会議の見出しだけで、制度が決まった・変わったとは判断しないでください。</p>
 <ul class="talk-list">{latest}</ul>
 <a class="more-link" href="{up}talk.html">話し合いの一覧を見る</a>
</section>"""

    def other_box(self, depth: int, items: list[dict], scope: str = "") -> str:
        """行事・広報・更新のお知らせなど、4種類に入らないもの。最新3件と一覧へのリンク。"""
        up = "../" * depth
        if not items:
            return ""
        latest = "".join(f'<li><a href="{e(it["url"])}" rel="noopener">{e(it["title"])}</a><span class="small">（{e(it["source_name"])}・{e(jdate(it.get("published")))}）</span></li>' for it in items[:3])
        return f"""<section class="box talk">
 <h2>{e(scope)}そのほかのお知らせ<span class="n">{len(items)}件</span></h2>
 <p class="small">行事や広報、ページ更新のお知らせなどです。見出しの言葉だけで分けているので、ここに役立つお知らせがまぎれていることもあります。</p>
 <ul class="talk-list">{latest}</ul>
 <a class="more-link" href="{up}other.html">そのほかのお知らせの一覧を見る</a>
</section>"""

    def list_by_day(self, items: list[dict], depth: int, limit: int | None = None) -> str:
        if not items:
            return '<p class="empty">いまのところ、この欄に出せる新しいお知らせはありません。国の新着の多くは会議の開催や行事・広報のお知らせで、制度の変更や申請の受け付けが出ない週もあります。下の「困ったときの相談先」もご覧ください。</p>'
        out = []
        cur = None
        for it in items[:limit] if limit else items:
            d = it.get("published") or it["first_seen"][:10]
            if d != cur:
                if cur is not None:
                    out.append("</ol>")
                out.append(f'<h2 class="day">{e(jdate(d))}</h2><ol class="cards">')
                cur = d
            out.append(self.card(it, depth))
        out.append("</ol>")
        return "\n".join(out)

    def deadlines_html(self) -> str:
        today = self.built.date().isoformat()
        live = sorted([d for d in self.deadlines if d.get("deadline", "") >= today], key=lambda d: d["deadline"])
        if not live:
            return ""
        rows = "".join(
            f'<li><span class="dl">{e(jdate(d["deadline"]))}まで</span> <a href="{e(d["url"])}" rel="noopener">{e(d["title"])}</a>'
            f'<span class="small">（{e(d.get("who", ""))}／{e(d["source_name"])}／{e(jdate(d["checked"], True))}に確認）</span></li>'
            for d in live)
        return f'<section class="box"><h2>もうすぐ締め切り（人が確かめたもの）</h2><ul class="deadlines">{rows}</ul></section>'

    def help_html(self, depth: int, topic: str | None = None) -> str:
        tools = [t for t in self.cfg.get("tools", []) if topic is None or topic in t.get("topics", [])]
        tool_html = "".join(f'<li class="tool"><a href="{e(t["url"])}" rel="noopener">{e(t["label"])}</a></li>' for t in tools)
        links = "".join(f'<li><a href="{e(l["url"])}" rel="noopener">{e(l["label"])}</a><span class="small">（{e(l["by"])}）</span></li>' for l in self.cfg.get("help_links", []))
        return f"""<section class="box help" id="help">
 <h2>困ったときの相談先</h2>
 <p>新着を読むより、まず相談したほうが早いこともあります。命の危険があるときは、迷わず110番・119番に電話してください。</p>
 <ul class="links">{tool_html}{links}</ul>
</section>"""

    def guides_html(self) -> str:
        """新着のトップの上のほうに出す、行動を助けるページ（家賃編・支える人へ）への入口。

        相談先の欄はページのいちばん下にあり、新着を60件読んだ先になるので、困って来た人も検索エンジンも
        見つけやすいよう、上のほうにも置く。
        """
        tools = [t for t in self.cfg.get("tools", []) if t.get("guide_title")]
        if not tools:
            return ""
        rows = "".join(f'<li class="tool"><a href="{e(t["url"])}">{e(t["guide_title"])}</a>'
                       + (f'<span class="small">{e(t["guide_note"])}</span>' if t.get("guide_note") else "") + "</li>" for t in tools)
        return f"""<section class="box help guides" aria-labelledby="guides-h">
 <h2 id="guides-h">困りごとを選ぶ</h2>
 <ul class="links">{rows}</ul>
 <p class="urgent-link small"><a href="{e(self.cfg.get("base_url", "") + "yachin/#safety")}">今夜寝る場所がない・今日の食べ物がない・命の危険・暴力から逃げたいときの相談先</a></p>
 <ul class="promise-plain small" aria-label="このサイトの約束"><li>無料です。登録はいらず、個人の情報も集めません。</li><li>制度の説明は、国などの公式資料で確かめ、出典を付けています。</li></ul>
</section>"""

    # ------------------------------------------------------------ ページ
    def page_index(self) -> str:
        desc = "家賃が払えない、仕事を失った、家族や友人を支えたいときに、今日やることと相談先、窓口で言える文を案内します。あわせて、生活・住まい・仕事・障害・子ども・高齢・外国籍の人に関わる国のお知らせの新着を、公式ページから毎日自動で集めて並べています。"
        body = [self.head("家賃・失業・支える人の今日やることと、支援制度の新着｜" + self.cfg.get("brand", self.cfg["name"]), desc, "index.html", 0), self.header(0, "", hero=True)]
        body.append('<main id="main" class="stack">')
        body.append(self.source_warnings())
        body.append(self.guides_html())
        # 2026-11-21 コタローさんの判断：トップの主役は3つの入口。新着はその下に続ける
        body.append('<h2 class="sec" id="news">国の支援の新着</h2><p class="small">国の機関の公式ページに出た新着を、1日2回自動で集めています。暮らし・住まい・仕事・子ども・外国籍の人などに関わるものだけを選び、見出しは公式のまま載せています。分野で絞り込めます。お知らせの種類と分野は、見出しの言葉からプログラムが自動で付けたもので、間違うことがあります。</p>')
        body.append(self.chips(0, None))
        body.append(self.deadlines_html())
        body.append(f'<h2 class="sec">暮らしに役立つお知らせ</h2><p class="small">{("制度の変更、申請や募集、相談窓口、注意の呼びかけの4種類だけを載せています。行事や広報は「そのほかのお知らせ」に、会議の開催は「国で話し合っていること」に分けています。" if self.rules.get("useful_kinds") else "制度の変更、申請や募集、相談窓口、注意の呼びかけなど。コンクール・表彰式・イベントなどの行事は「そのほかのお知らせ」に、会議の開催は「国で話し合っていること」に分けています。") if self.other else "制度の変更、申請や募集、相談窓口、注意の呼びかけなど。会議の開催は下の「国で話し合っていること」に分けています。"}</p>')
        body.append(self.list_by_day(self.useful, 0, limit=60))
        if len(self.useful) > 60:
            body.append('<p class="more">もっと前のお知らせは、上の分野ボタンから見られます。</p>')
        body.append(self.other_box(0, self.other))
        body.append(self.talk_box(0, self.talk))
        body.append(self.help_html(0))
        body.append("</main>")
        body.append(self.footer(0))
        return "\n".join(body)

    def page_topic(self, slug: str) -> str:
        t = self.topics[slug]
        items = [it for it in self.useful if slug in it["topics"]]
        talk = [it for it in self.talk if slug in it["topics"]]
        other = [it for it in self.other if slug in it["topics"]]
        desc = f"「{t['label']}」に関わる国の支援やお知らせの新着です。公式ページから毎日自動で集めています。"
        body = [self.head(f"{t['label']}の支援制度・お知らせ 新着｜{self.cfg.get('brand', self.cfg['name'])}", desc, f"t/{slug}.html", 1), self.header(1, f"t/{slug}.html")]
        body.append('<main id="main" class="stack">')
        body.append(self.source_warnings())
        body.append(f'<h1 class="h1">{e(t["label"])}の新着</h1><p class="lead">{e(desc)}</p>')
        body.append(self.chips(1, slug))
        body.append('<h2 class="sec">暮らしに役立つお知らせ</h2>')
        body.append(self.list_by_day(items, 1))
        body.append(self.other_box(1, other, f"{t['label']}の"))
        body.append(self.talk_box(1, talk, f"{t['label']}について、"))
        body.append(self.help_html(1, slug))
        body.append("</main>")
        body.append(self.footer(1))
        return "\n".join(body)

    def page_talk(self) -> str:
        desc = "審議会・検討会の開催、意見募集、調査の結果など、国で話し合っていることの一覧です。検討段階の案と、すでに公表された調査結果などを含みます。支援を利用できるかは、公式ページで確かめてください。"
        body = [self.head(f"国で話し合っていること（審議会・意見募集）｜{self.cfg.get('brand', self.cfg['name'])}", desc, "talk.html", 0), self.header(0, "talk.html")]
        body.append('<main id="main" class="stack">')
        body.append(self.source_warnings())
        body.append(f'<h1 class="h1">国で話し合っていること</h1><p class="lead">{e(desc)}</p>')
        body.append(self.list_by_day(self.talk, 0, limit=150))
        body.append(self.help_html(0))
        body.append("</main>")
        body.append(self.footer(0))
        return "\n".join(body)

    def page_other(self) -> str:
        desc = "行事や広報、ページ更新のお知らせなど、制度の変更・申請・相談窓口・注意のどれにも当たらない国のお知らせの一覧です。"
        body = [self.head(f"そのほかのお知らせ｜{self.cfg.get('brand', self.cfg['name'])}", desc, "other.html", 0), self.header(0, "other.html")]
        body.append('<main id="main" class="stack">')
        body.append(self.source_warnings())
        body.append(f'<h1 class="h1">そのほかのお知らせ</h1><p class="lead">{e(desc)}</p><p class="small">見出しの言葉だけで分けているので、役立つお知らせがここにまぎれていることもあります。</p>')
        body.append(self.list_by_day(self.other, 0, limit=150))
        body.append(self.help_html(0))
        body.append("</main>")
        body.append(self.footer(0))
        return "\n".join(body)

    def page_about(self) -> str:
        rows = []
        for sid, s in self.sources.items():
            h = self.health.get("sources", {}).get(sid, {})
            if h.get("failures", 0) >= 1:
                state = f'取れていません（{e(h.get("failures"))}回つづけて失敗）'
            elif h.get("last_ok"):
                state = "正常"
            else:
                state = "まだ取得していません"
            last = h.get("last_ok")
            last_t = jdate(last[:10], True) if last else "—"
            rows.append(f'<tr><td><a href="{e(s["url"])}" rel="noopener">{e(s["name"])}</a></td><td>{state}</td><td>{e(last_t)}</td>'
                        f'<td><a href="{e(s["license_url"])}" rel="noopener">{e(s["license"])}</a></td></tr>')
        kinds = "".join(f'<li><b>{e(k["label"])}</b>：{e(k.get("about") or k["hint"])}</li>' for k in list(self.kinds.values()))
        desc = "このページのしくみ、出典、情報源の状態、利用条件について。"
        survey_li = ('<li>感想のアンケート（任意）は、押した人だけが外部のフォームを開きます。くわしくは'
                     '<a href="unei.html#kansou">アンケートについて</a>。</li>') if self.survey() else ""
        body = [self.head(f"このページについて｜{self.cfg.get('brand', self.cfg['name'])}", desc, "about.html", 0), self.header(0, "about.html")]
        body.append(f"""<main id="main" class="prose">
<h1 class="h1">このページについて</h1>
<h2>何をしているページか</h2>
<p>国の機関の公式ページに出る「新着情報」の一覧を、プログラムが1日2回読みに行き、生活・住まい・仕事・障害・子ども・高齢・外国籍の人などに関わりそうなものだけを選んで並べています。</p>
<ul>
<li><b>見出しは公式のまま</b>です。機械（AIを含む）による要約や言い換えはしていません。要約の誤りで困る人が出ないようにするためです。</li>
<li><b>分野と種類は、見出しの言葉から機械的に付けています</b>。たとえば見出しに「審議会」があれば「検討中」に分けます。分け方が外れることもあります。</li>
<li>「やさしい説明」や「もうすぐ締め切り」は、<b>人が公式ページを読んで確かめたものだけ</b>を、確かめた日と一緒に載せています。</li>
<li>職員の採用、入札、議事録、記者会見など、暮らしの支援と関わりが薄いお知らせは除いています。</li>
<li>このページは利用する人の情報を集めていません。アクセス解析や広告のプログラムも入れていません。見出しに使っている書体（Zen Maru Gothic）もこのサイトの中に置いているので、ページを開いてもほかの会社のサーバーには接続しません（公開に使っている GitHub を除く）。</li>{survey_li}
</ul>
<h2>種類の見方</h2>
<ul>{kinds}</ul>
<h2>情報源と、いまの状態</h2>
<div class="tablewrap"><table><thead><tr><th>情報源</th><th>状態</th><th>最後に取れた日</th><th>利用条件</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p>見出しとリンクは、各機関のホームページのコンテンツを「公共データ利用規約（第1.0版）」に従って利用しています（出典は各項目に表示）。このページは各機関の見出しを選んで並べ替えたもので、各機関が作成したものではありません。</p>
<h2>注意</h2>
<p>情報が古くなっていたり、取りこぼしたりすることがあります。大事なことは、必ずリンク先の公式ページや窓口で確かめてください。このページが{e(self.cfg.get('stale_hours', 72))}時間以上更新されていないときは、画面の上にお知らせを出します。</p>
{self.help_html(0)}
</main>""")
        body.append(self.footer(0))
        return "\n".join(body)

    def feed(self) -> str:
        base = self.cfg["base_url"]
        upd = self.store.get("updated") or self.built.isoformat(timespec="seconds")
        entries = []
        for it in self.useful[:50]:
            d = (it.get("published") or it["first_seen"][:10]) + "T00:00:00+09:00"
            k = self.kinds.get(it["kind"], self.rules["default_kind"])["label"]
            tl = "・".join(self.topics[t]["label"] for t in it["topics"] if t in self.topics)
            entries.append(f"""<entry><title>{e(it['title'])}</title><link href="{e(it['url'])}"/><id>urn:shien-news:{e(it['id'])}</id><updated>{e(d)}</updated>
<summary>{e(f'【{k}】{tl}／{it["source_name"]}')}</summary><author><name>{e(it['source_name'])}</name></author></entry>""")
        return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>{e(self.cfg['name'])}</title><link href="{e(base)}"/><link rel="self" href="{e(urljoin(base, 'feed.xml'))}"/>
<id>{e(base)}</id><updated>{e(upd)}</updated>
<rights>見出しとリンクは各機関のホームページから（公共データ利用規約 第1.0版）。各機関が作成したものではありません。</rights>
{''.join(entries)}
</feed>
"""

    def copy_pages(self, out: Path, src: Path | None = None) -> list[str]:
        """pages/（行動支援の試作と運営者ページ）を site/ にそのまま写す。写したHTMLのパスを返す。

        運営者ページに「【未記入】」が残っている間は、どれも写さない（仮のまま公開しないため）。
        生成したページと同じ名前のファイルは上書きしない。
        """
        src = src or ROOT / "pages"
        if not src.is_dir():
            return []
        unei = src / "unei.html"
        if not unei.exists() or PLACEHOLDER in unei.read_text(encoding="utf-8"):
            print(f"pages/ は公開しません：運営者ページ（pages/unei.html）に「{PLACEHOLDER}」が残っています", file=sys.stderr)
            return []
        copied = []
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            dst = out / rel
            if dst.exists():
                print(f"pages/{rel.as_posix()} は生成したページと同じ名前なので写しません", file=sys.stderr)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix == ".html":
                depth = len(rel.parts) - 1
                text = f.read_text(encoding="utf-8")
                if MENU_MARK in text:
                    text = text.replace(MENU_MARK, menu_html(depth, rel.as_posix()), 1)
                text = SURVEY_MARK.sub(lambda m: self.survey_unei() if m.group(1) == "unei" else self.survey_block(m.group(1), depth), text)
                if rel.as_posix() == "yachin/index.html":
                    text = prerender_yachin(text)
                text = add_csp(text)
                dst.write_text(text, encoding="utf-8")
            else:
                shutil.copy2(f, dst)
            if f.suffix == ".html":
                p = rel.as_posix()
                copied.append(p[: -len("index.html")] if p.endswith("/index.html") else p)
        return copied

    def build(self, out: Path) -> None:
        if out.exists():
            shutil.rmtree(out)
        (out / "t").mkdir(parents=True)
        shutil.copytree(ROOT / "assets", out / "assets")
        (out / "index.html").write_text(self.page_index(), encoding="utf-8")
        (out / "about.html").write_text(self.page_about(), encoding="utf-8")
        (out / "talk.html").write_text(self.page_talk(), encoding="utf-8")
        # サイトマップの lastmod は「中身が実際に変わった日」に近づける（毎回の生成日にすると Google が当てにしなくなる）。
        # お知らせの一覧は載っている中でいちばん新しい日付、情報源の状態を出す about は生成日、
        # pages/ から写すページは変わった日をここでは知れないので書かない（書かなくてもよい項目）。
        pages = [("index.html", self.newest(self.useful + self.other + self.talk)),
                 ("talk.html", self.newest(self.talk)),
                 ("about.html", self.built.date().isoformat())]
        if self.other:
            (out / "other.html").write_text(self.page_other(), encoding="utf-8")
            pages.append(("other.html", self.newest(self.other)))
        for slug in self.topics:
            (out / "t" / f"{slug}.html").write_text(self.page_topic(slug), encoding="utf-8")
            pages.append((f"t/{slug}.html", self.newest([it for it in self.items if slug in it["topics"]])))
        (out / "feed.xml").write_text(self.feed(), encoding="utf-8")
        pages += [(p, None) for p in self.copy_pages(out)]
        base = self.cfg["base_url"]
        urls = "".join(f"<url><loc>{e(self.url(p))}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>" for p, m in pages)
        (out / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")
        # 注意：github.io のプロジェクトサイト（…/shien-news/）では、検索エンジンが読むのはホスト直下の robots.txt だけなので
        # この robots.txt は効かない。独自ドメインに移したときのために残している。サイトマップは Search Console から送る。
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {urljoin(base, 'sitemap.xml')}\n", encoding="utf-8")
        vfile = self.cfg.get("google_site_verification_file", "")
        if vfile:
            if not VERIFY_FILE.fullmatch(vfile):
                raise ValueError("config/site.json の google_site_verification_file は google…….html というファイル名だけを書いてください")
            # Google が配る確認用ファイルと同じ中身（サイトマップには載せない）
            (out / vfile).write_text(f"google-site-verification: {vfile}", encoding="utf-8")
        (out / ".nojekyll").write_text("", encoding="utf-8")
        save_json(out / "items.json", {"updated": self.store.get("updated"), "license_note": "見出しとリンクは各機関のホームページから（公共データ利用規約 第1.0版）", "items": self.items})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()
    Site(Path(a.data), sample=a.sample).build(Path(a.out))
    print("built", a.out)


if __name__ == "__main__":
    main()
