"""data/ の内容から静的なサイト（site/）を作る。JavaScriptがなくても読めるHTMLを出す。

使い方: python scripts/build.py [--out site] [--sample]
--sample は表示確認用。画面の上に「見本のデータ」と大きく出す。
"""
from __future__ import annotations

import argparse
import html
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import JST, ROOT, load_json, now_jst, save_json  # noqa: E402

WD = "月火水木金土日"
PLACEHOLDER = "【未記入"


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def jdate(iso: str | None, with_year=False) -> str:
    if not iso:
        return "日付なし"
    d = date.fromisoformat(iso[:10])
    s = f"{d.month}月{d.day}日（{WD[d.weekday()]}）"
    return f"{d.year}年{s}" if with_year else s


def visible(it: dict) -> bool:
    return it.get("publish") == "auto" and not it.get("excluded") and bool(it.get("topics"))


class Site:
    def __init__(self, data_dir: Path, sample: bool = False):
        self.cfg = load_json(ROOT / "config" / "site.json", {})
        self.rules = load_json(ROOT / "config" / "rules.json", {})
        self.sources = {s["id"]: s for s in load_json(ROOT / "config" / "sources.json", {"sources": []})["sources"]}
        self.store = load_json(data_dir / "items.json", {"items": [], "updated": None})
        self.health = load_json(data_dir / "health.json", {"sources": {}})
        self.notes = {n["url"]: n for n in load_json(data_dir / "notes.json", {"notes": []})["notes"]}
        self.deadlines = load_json(data_dir / "deadlines.json", {"deadlines": []})["deadlines"]
        self.sample = sample
        self.topics = {t["slug"]: t for t in self.rules["topics"]}
        self.kinds = {k["id"]: k for k in self.rules["kinds"]}
        self.kinds[self.rules["default_kind"]["id"]] = self.rules["default_kind"]
        self.items = [it for it in self.store["items"] if visible(it)]
        talk_kinds = {k["id"] for k in self.rules["kinds"] if k.get("group") == "talk"}
        useful_kinds = set(self.rules.get("useful_kinds", []))
        # 3つに分ける。暮らしに役立つお知らせ（制度の変更・募集/申請・相談窓口・注意）、
        # 国で話し合っていること（会議・意見募集・調査）、そのほかのお知らせ（行事・広報など）
        self.useful = [it for it in self.items if it["kind"] in useful_kinds]
        self.talk = [it for it in self.items if it["kind"] in talk_kinds]
        self.other = [it for it in self.items if it["kind"] not in useful_kinds and it["kind"] not in talk_kinds]
        self.built = now_jst()
        oks = [h.get("last_ok") for h in self.health.get("sources", {}).values() if h.get("last_ok")]
        # 「最後に情報源から実際に取れた時刻」。ページを作り直しただけでは新しくならない
        self.fresh = max(oks) if oks else ""

    # ------------------------------------------------------------ 部品
    def head(self, title: str, desc: str, path: str, depth: int) -> str:
        up = "../" * depth
        canon = urljoin(self.cfg["base_url"], path)
        return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(canon)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(canon)}">
<meta name="theme-color" content="#fffaf2">
<link rel="alternate" type="application/atom+xml" title="{e(self.cfg['name'])}" href="{up}feed.xml">
<link rel="stylesheet" href="{up}assets/fonts.css">
<link rel="stylesheet" href="{up}assets/tokens.css">
<link rel="stylesheet" href="{up}assets/site.css">
</head>
<body data-generated="{e(self.fresh)}" data-stale-hours="{e(self.cfg.get('stale_hours', 72))}">
<a class="skip" href="#main">本文へ</a>
"""

    def header(self, depth: int, hero: bool = False) -> str:
        up = "../" * depth
        sample = ""
        if self.sample:
            sample = '<div class="sample">画面の確認用の見本です。並んでいる項目は2026年11月12〜13日に各機関の新着一覧に実際に出ていた見出しの一部で、公開後は毎日の自動取得の結果に置き換わります。</div>'
        brand = e(self.cfg.get("brand", self.cfg["name"]))
        logo = '<svg class="logo" viewBox="0 0 30 30" aria-hidden="true" focusable="false"><rect x="7" y="3" width="17" height="25" rx="2" fill="var(--c-sign-accent)" stroke="var(--c-sign-ink)" stroke-width="2.5"/><path d="M7 3 L15 6 L15 30 L7 28 Z" fill="var(--c-sign-ink)"/><circle cx="13" cy="17" r="1.4" fill="var(--c-sign)"/></svg>'
        path = '<svg class="sign-path" viewBox="0 0 360 40" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path d="M0 34 C 90 34, 150 10, 330 14" stroke="var(--c-sign-accent)" stroke-width="10" fill="none" stroke-linecap="round"/><path d="M0 34 C 90 34, 150 10, 330 14" stroke="rgba(0,0,0,0.22)" stroke-width="10" fill="none" stroke-dasharray="2 6"/></svg>'
        if hero:
            top = f"""<header class="hero">
 <div class="brandrow">{logo}<span class="brand">{brand}</span><span class="tag">新着まとめ</span></div>
 <h1>国の支援の<em>新着</em>を、<br>毎日まとめて。</h1>
 <p class="lead">暮らし・住まい・仕事・子ども・外国籍の人などに関わるものだけを選んでいます。</p>
 <ul class="promise" aria-label="このページの約束">
  <li>国の公式ページの見出しを、そのまま載せています</li><li>無料です。登録はいらず、個人の情報も集めません</li>
 </ul>
 <a class="jump" href="#help">いま困っていることがある人は、相談先へ ↓</a>
 {path}
</header>"""
        else:
            top = f"""<header class="hero hero-sub">
 <div class="brandrow">{logo}<a class="brand" href="{up}index.html">{brand} 新着</a></div>
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
  <p>最後に確認した時刻：{e(self.updated_text())}</p>
</footer>
</div>
<script src="{up}assets/site.js" defer></script>
</body>
</html>
"""

    def updated_text(self) -> str:
        u = self.store.get("updated")
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
            out.append(f'<a class="chip" href="{up}t/{slug}.html"{cls}>{e(t["label"])}<span class="n">{counts.get(slug, 0)}</span></a>')
        out.append("</nav>")
        return "\n".join(out)

    def card(self, it: dict, depth: int) -> str:
        k = self.kinds.get(it["kind"], self.rules["default_kind"])
        tags = "".join(f'<li>{e(self.topics[t]["label"])}</li>' for t in it["topics"] if t in self.topics)
        extra = ""
        if it.get("business"):
            extra += '<li class="biz">事業者向けかもしれません</li>'
        note = self.notes.get(it["url"])
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
 <p class="small">審議会や検討会の開催、意見募集、調査の結果です。<b>まだ決まっていないことで、いま使える支援ではありません。</b>これから制度が変わるかもしれない「予告」として見てください。</p>
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

    # ------------------------------------------------------------ ページ
    def page_index(self) -> str:
        desc = "生活・住まい・仕事・障害・子ども・高齢・外国籍の人に関わる国のお知らせの新着を、公式ページから毎日自動で集めて分野ごとに並べています。"
        body = [self.head("生活・住まい・仕事の支援制度 新着まとめ｜" + self.cfg.get("brand", self.cfg["name"]), desc, "index.html", 0), self.header(0, hero=True)]
        body.append('<main id="main" class="stack">')
        body.append(self.source_warnings())
        body.append(self.chips(0, None))
        body.append(self.deadlines_html())
        body.append('<h2 class="sec">暮らしに役立つお知らせ</h2><p class="small">制度の変更、申請や募集、相談窓口、注意の呼びかけの4種類だけを載せています。行事や広報は「そのほかのお知らせ」に、会議の開催は「国で話し合っていること」に分けています。</p>')
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
        body = [self.head(f"{t['label']}の支援制度・お知らせ 新着｜{self.cfg.get('brand', self.cfg['name'])}", desc, f"t/{slug}.html", 1), self.header(1)]
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
        desc = "審議会・検討会の開催、意見募集、調査の結果など、国で話し合っていることの一覧です。まだ決まっていないことで、いま使える支援ではありません。"
        body = [self.head(f"国で話し合っていること（審議会・意見募集）｜{self.cfg.get('brand', self.cfg['name'])}", desc, "talk.html", 0), self.header(0)]
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
        body = [self.head(f"そのほかのお知らせ｜{self.cfg.get('brand', self.cfg['name'])}", desc, "other.html", 0), self.header(0)]
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
        kinds = "".join(f'<li><b>{e(k["label"])}</b>：{e(k["hint"])}</li>' for k in list(self.kinds.values()))
        desc = "このページのしくみ、出典、情報源の状態、利用条件について。"
        body = [self.head(f"このページについて｜{self.cfg.get('brand', self.cfg['name'])}", desc, "about.html", 0), self.header(0)]
        body.append(f"""<main id="main" class="prose">
<h1 class="h1">このページについて</h1>
<h2>何をしているページか</h2>
<p>国の機関の公式ページに出る「新着情報」の一覧を、プログラムが1日2回読みに行き、生活・住まい・仕事・障害・子ども・高齢・外国籍の人などに関わりそうなものだけを選んで並べています。</p>
<ul>
<li><b>見出しは公式のまま</b>です。機械（AIを含む）による要約や言い換えはしていません。要約の誤りで困る人が出ないようにするためです。</li>
<li><b>分野と種類は、見出しの言葉から機械的に付けています</b>。たとえば見出しに「審議会」があれば「検討中」に分けます。分け方が外れることもあります。</li>
<li>「やさしい説明」や「もうすぐ締め切り」は、<b>人が公式ページを読んで確かめたものだけ</b>を、確かめた日と一緒に載せています。</li>
<li>職員の採用、入札、議事録、記者会見など、暮らしの支援と関わりが薄いお知らせは除いています。</li>
<li>このページは利用する人の情報を集めていません。アクセス解析や広告のプログラムも入れていません。見出しに使っている書体（Zen Maru Gothic）もこのサイトの中に置いているので、ページを開いてもほかの会社のサーバーには接続しません（公開に使っている GitHub を除く）。</li>
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
        (out / "other.html").write_text(self.page_other(), encoding="utf-8")
        pages = ["index.html", "other.html", "talk.html", "about.html"]
        for slug in self.topics:
            (out / "t" / f"{slug}.html").write_text(self.page_topic(slug), encoding="utf-8")
            pages.append(f"t/{slug}.html")
        (out / "feed.xml").write_text(self.feed(), encoding="utf-8")
        pages += self.copy_pages(out)
        base = self.cfg["base_url"]
        lastmod = self.built.date().isoformat()
        urls = "".join(f"<url><loc>{e(urljoin(base, p))}</loc><lastmod>{lastmod}</lastmod></url>" for p in pages)
        (out / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")
        (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {urljoin(base, 'sitemap.xml')}\n", encoding="utf-8")
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
