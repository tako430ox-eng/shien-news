"""取得・解析・分類・サイト生成・変更監視のテスト。ネットワークは使わず、手元の見本ページを小さなサーバーで配る。

実行: python -m unittest discover -s tests -v
"""
import http.server
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import lib  # noqa: E402
import build  # noqa: E402
import update  # noqa: E402
import watch  # noqa: E402

RULES = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
SOURCES = {s["id"]: s for s in json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]}


def parse_fixture(sid, name):
    s = SOURCES[sid]
    raw = (FX / name).read_bytes()
    return lib.parse_html_list(lib.decode(raw, {}), s["url"], s["item_href"], s.get("strip_prefixes", []), s.get("exclude_href"))


class TestDates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(str(lib.find_date("2026年11月12日（木）掲載")), "2026-11-12")
        self.assertEqual(str(lib.find_date("令和8年11月12日")), "2026-11-12")
        self.assertEqual(str(lib.find_date("令和元年5月1日")), "2019-05-01")
        self.assertEqual(str(lib.find_date("２０２６年１１月３日")), "2026-11-03")  # 全角数字
        self.assertEqual(str(lib.find_date("2026/11/12")), "2026-11-12")
        self.assertIsNone(lib.find_date("日付なし"))
        self.assertIsNone(lib.find_date("2026年13月40日"))


class TestHtmlLists(unittest.TestCase):
    def test_mhlw_day_headings(self):
        items = parse_fixture("mhlw", "mhlw_newinfo.html")
        urls = [i["url"] for i in items]
        # header/footer/nav/script の中は読まない
        self.assertNotIn("https://www.mhlw.go.jp/stf/newpage_00001.html", urls)
        self.assertNotIn("https://www.mhlw.go.jp/stf/newpage_99999.html", urls)
        self.assertEqual(len(items), 6)
        first = items[1]
        self.assertTrue(first["title"].startswith("【テスト用の架空の見出し】住居確保給付金"))
        self.assertFalse(first["title"].endswith("NEW"))
        self.assertEqual(first["date"], "2026-11-13")
        self.assertEqual(first["category"], "報道発表")
        # 次の日付見出しのあとは、その日付になる
        self.assertEqual(items[2]["date"], "2026-11-12")
        self.assertEqual(items[2]["url"], "https://www.mhlw.go.jp/stf/newpage_77378.html")
        # 見出しの中に日付があっても（議事録の開催日）、掲載日を取り違えない
        self.assertEqual(items[3]["date"], "2026-11-12")
        self.assertTrue(items[3]["title"].startswith("2026年10月26日"))

    def test_cfa_date_inside_link(self):
        items = parse_fixture("cfa", "cfa_news.html")
        self.assertEqual(len(items), 4)  # 「次へ」は除外
        self.assertEqual(items[0]["date"], "2026-11-12")
        self.assertTrue(items[0]["title"].startswith("みて かんじて"))
        self.assertTrue(items[0]["title"].endswith("にて開催"))  # 見出しの中の「令和8年12月19日」を掲載日と取り違えない
        self.assertEqual(items[1]["date"], "2026-11-10")
        self.assertEqual(items[1]["category"], "報道発表")

    def test_isa_shift_jis_and_date_before(self):
        items = parse_fixture("isa", "isa_newslist.html")
        self.assertEqual(len(items), 3)  # 年別一覧へのリンクと、日付のないメニューのリンクは除外
        self.assertEqual(items[0]["date"], "2026-11-11")
        self.assertEqual(items[0]["category"], "調達・採用情報")
        self.assertEqual(items[1]["title"], "生活オリエンテーション動画（ショート版）を配信しています！")


class TestFeeds(unittest.TestCase):
    def test_rss2(self):
        items = lib.parse_feed((FX / "digital.xml").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["date"], "2026-11-12")
        self.assertEqual(items[0]["category"], "意見募集")

    def test_rss1(self):
        items = lib.parse_feed((FX / "rss1.rdf").read_text(encoding="utf-8"))
        self.assertEqual(items, [{"title": "テストの見出し", "url": "https://example.go.jp/a", "date": "2026-11-12", "category": ""}])


class TestClassify(unittest.TestCase):
    def c(self, t, cat=""):
        return lib.classify(t, RULES, cat)

    def test_topics_and_kinds(self):
        r = self.c("住居確保給付金の支給要件の見直しについて")
        self.assertIn("housing", r["topics"])
        self.assertIn("money", r["topics"])
        self.assertEqual(r["kind"], "change")
        self.assertEqual(self.c("第5回ひとり親家庭支援ワーキンググループの開催について")["kind"], "discussion")
        self.assertEqual(self.c("生活保護の申請を装った詐欺にご注意ください")["kind"], "warning")
        self.assertIn("foreign", self.c("生活オリエンテーション動画（ショート版）を配信しています")["topics"])
        # 会議・調査は「話し合い」の種類になる（2026-11-13 の本番データの例）
        for t in ["第13回自殺総合対策の推進に関する有識者会議資料", "第26回成年後見制度利用促進専門家会議　開催案内",
                  "補償金・優生手術等一時金認定審査部会（第21回）の議事要旨を掲載しました", "育成就労に関する二国間取決め（協力覚書）",
                  "令和８年度に実施した出入国在留管理行政に係る関係者ヒアリングの結果概要を掲載しました。"]:
            self.assertIn(self.c(t)["kind"], ("discussion", "survey"), t)
        self.assertEqual(self.c("生活保護の申請を装った詐欺にご注意ください")["kind"], "warning")
        # 2026-11-13 の本番取得で見つかった誤り：システムの「障害」を障害の分野にしていた
        self.assertNotIn("disability", self.c("Microsoft365の大規模障害について")["topics"])
        self.assertTrue(self.c("マイクロソフト社の障害によりメール不着等となる事象について")["excluded"])
        self.assertIn("disability", self.c("障害者の就労支援について")["topics"])
        # 「取組開始から8年目」のような言い回しを「制度の変更」にしない（2026-11-13 の本番データの例）
        self.assertNotEqual(self.c("応援サポーターが登場　取組開始から8年目")["kind"], "change")
        self.assertEqual(self.c("令和８年１０月１日付け永住許可に関するガイドラインを改定しました。")["kind"], "change")

    def test_excluded(self):
        self.assertTrue(self.c("上野厚生労働大臣 閣議後記者会見のお知らせ")["excluded"])
        self.assertTrue(self.c("第137回社会保障審議会介護保険部会 議事録")["excluded"])
        self.assertTrue(self.c("採用に関するお知らせを更新しました", "調達・採用情報")["excluded"])
        self.assertFalse(self.c("児童扶養手当のオンライン申請の受付を開始しました")["excluded"])

    def test_unrelated_has_no_topic(self):
        self.assertEqual(self.c("原子力災害対策特別措置法第20条第2項の規定に基づく食品の出荷制限の解除")["topics"], [])


class FixtureServer:
    def __enter__(self):
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(self.dir), **k)  # noqa: E731
        handler.log_message = lambda *a: None
        self.dir = Path(tempfile.mkdtemp())
        for f in FX.iterdir():
            shutil.copy(f, self.dir / f.name)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.dir)


class TestEndToEnd(unittest.TestCase):
    """見本ページを配るサーバーから取得 → 保存 → サイト生成まで通す。"""

    def setUp(self):
        os.environ["SHIEN_SLEEP"] = "0"
        os.environ.pop("NO_PROXY", None)
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        self.tmp = Path(tempfile.mkdtemp())
        for n in ("notes.json", "deadlines.json"):
            shutil.copy(ROOT / "data" / n, self.tmp / n)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        os.environ.pop("SHIEN_SOURCE_OVERRIDE", None)

    def test_update_and_build(self):
        with FixtureServer() as srv:
            ov = {
                "mhlw": {"url": srv.url("mhlw_newinfo.html"), "item_href": "/stf/"},
                "cfa": {"url": srv.url("cfa_news.html"), "item_href": "/(press|policies)/"},
                "isa": {"url": srv.url("isa_newslist.html"), "item_href": "/isa/"},
                "digital": {"url": srv.url("missing.xml")},  # 404 → 失敗として記録されるはず
            }
            os.environ["SHIEN_SOURCE_OVERRIDE"] = json.dumps(ov)
            s1 = update.run(ROOT / "config" / "sources.json", self.tmp)
            self.assertTrue(s1["sources"]["mhlw"]["ok"])
            self.assertFalse(s1["sources"]["digital"]["ok"])
            self.assertEqual(s1["new"], 6 + 4 + 3)
            # 2回目は新着ゼロ（重複しない）
            s2 = update.run(ROOT / "config" / "sources.json", self.tmp)
            self.assertEqual(s2["new"], 0)
            health = json.loads((self.tmp / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["sources"]["digital"]["failures"], 2)
            self.assertEqual(health["sources"]["mhlw"]["failures"], 0)

        out = self.tmp / "site"
        site = build.Site(self.tmp)
        site.build(out)
        index = (out / "index.html").read_text(encoding="utf-8")
        # 関係の薄いもの（採用・会見・議事録）は出さない
        self.assertNotIn("閣議後記者会見", index)
        self.assertNotIn("採用選考", index)
        self.assertNotIn("議事録", index)
        # 関係のあるものは出す。公式へのリンクと出典つき
        self.assertIn("住居確保給付金の支給要件の見直し", index)
        self.assertIn("出典：厚生労働省ホームページ", index)
        # useful_kinds を書かない今の設定では、話し合い以外はすべて役立つ欄に出し、「そのほか」は作らない
        self.assertIn("生活オリエンテーション動画", index.split("class=\"box talk\"")[0])
        self.assertFalse((out / "other.html").exists())
        self.assertNotIn("other.html", (out / "sitemap.xml").read_text(encoding="utf-8"))
        # 会議は「暮らしに役立つお知らせ」に混ぜず、「国で話し合っていること」に分ける
        useful_part = index.split("国で話し合っていること")[0]
        self.assertNotIn("ひとり親家庭支援ワーキンググループ", useful_part)
        self.assertIn("ひとり親家庭支援ワーキンググループ", (out / "talk.html").read_text(encoding="utf-8"))
        self.assertIn("検討段階の案と、すでに公表された調査結果などを含みます", index)
        self.assertNotIn("ワーキンググループ", (out / "feed.xml").read_text(encoding="utf-8"))
        # 2回続けて失敗した情報源は画面に警告を出す
        self.assertIn("デジタル庁の新着が、いま自動で取れていません", index)
        # 分野ページ・フィード・サイトマップ
        self.assertTrue((out / "t" / "housing.html").exists())
        self.assertIn("<entry>", (out / "feed.xml").read_text(encoding="utf-8"))
        self.assertIn("t/housing.html", (out / "sitemap.xml").read_text(encoding="utf-8"))
        # 鮮度の基準は「最後に実際に取れた時刻」
        self.assertIn(f'data-generated="{site.fresh}"', index)
        self.assertTrue(site.fresh)

    def test_rules_applied_to_old_items(self):
        """規則を変えたら、もう一覧から消えた記事にも新しい規則が当たる。"""
        (self.tmp / "items.json").write_text(json.dumps({"updated": None, "items": [{
            "id": "old1", "source": "isa", "source_name": "出入国在留管理庁", "title": "在留許可手数料の額の改定がされます。",
            "url": "https://www.moj.go.jp/isa/x.html", "published": "2099-01-01", "category": "", "first_seen": "2099-01-01T00:00:00+09:00",
            "last_seen": "2099-01-01T00:00:00+09:00", "publish": "auto", "topics": ["foreign"], "kind": "notice", "excluded": False, "business": False}]}),
            encoding="utf-8")
        with FixtureServer() as srv:
            os.environ["SHIEN_SOURCE_OVERRIDE"] = json.dumps({k: {"url": srv.url("missing.html")} for k in SOURCES})
            update.run(ROOT / "config" / "sources.json", self.tmp)
        it = json.loads((self.tmp / "items.json").read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(it["kind"], "change")

    def test_html_escape(self):
        (self.tmp / "items.json").write_text(json.dumps({"updated": None, "items": [{
            "id": "x", "source": "mhlw", "source_name": "厚生労働省", "title": "<script>alert(1)</script>生活保護",
            "url": "https://www.mhlw.go.jp/\"onmouseover=\"x", "published": "2026-11-12", "first_seen": "2026-11-12T00:00:00+09:00",
            "publish": "auto", "topics": ["money"], "kind": "notice", "excluded": False, "business": False}]}), encoding="utf-8")
        out = self.tmp / "site"
        build.Site(self.tmp).build(out)
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", index)
        self.assertIn("&lt;script&gt;", index)
        self.assertNotIn('"onmouseover="', index)


class TestWatch(unittest.TestCase):
    def test_detects_change_and_unreachable(self):
        os.environ["SHIEN_SLEEP"] = "0"
        tmp = Path(tempfile.mkdtemp())
        with FixtureServer() as srv:
            page = srv.dir / "page.html"
            page.write_text("<html><body><header>メニュー</header><p>家賃の上限は5万円です。</p><p>窓口は市役所です。</p></body></html>", encoding="utf-8")
            cfg = {"used_by": "test", "ignore": [], "pages": [
                {"url": srv.url("page.html"), "what": "見本"},
                {"url": srv.url("gone.html"), "what": "消えたページ"}]}
            (tmp / "w.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            r = tmp / "report.md"
            ch1 = watch.run(tmp / "w.json", tmp / "state.json", r)
            self.assertEqual([c["type"] for c in ch1], ["unreachable"])  # 初回は比べる相手がないので変化なし
            page.write_text("<html><body><header>メニュー変更</header><p>家賃の上限は6万円です。</p><p>窓口は市役所です。</p></body></html>", encoding="utf-8")
            ch2 = watch.run(tmp / "w.json", tmp / "state.json", r)
            changed = [c for c in ch2 if c["type"] == "changed"]
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["added"], ["家賃の上限は6万円です。"])  # ヘッダーの変化は無視
            self.assertIn("6万円", r.read_text(encoding="utf-8"))
            state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertNotIn("家賃", json.dumps(state, ensure_ascii=False))  # 本文は保存しない
        shutil.rmtree(tmp)


class TestPages(unittest.TestCase):
    """pages/（行動支援の試作と運営者ページ）の公開。運営者ページが仮のままなら公開しない。外部から読み込まない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "pages", self.tmp / "pages")
        self.site = build.Site(ROOT / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fill(self):
        u = self.tmp / "pages" / "unei.html"
        u.write_text(u.read_text(encoding="utf-8").replace(build.PLACEHOLDER, "【記入済み"), encoding="utf-8")

    def test_not_copied_while_placeholder(self):
        u = self.tmp / "pages" / "unei.html"
        u.write_text(u.read_text(encoding="utf-8") + "<!-- " + build.PLACEHOLDER + "：テスト】 -->", encoding="utf-8")
        out = self.tmp / "site"
        out.mkdir()
        self.assertEqual(self.site.copy_pages(out, self.tmp / "pages"), [])
        self.assertFalse((out / "yachin").exists())
        self.assertFalse((out / "unei.html").exists())

    def test_copied_when_filled_and_no_overwrite(self):
        self.fill()
        (self.tmp / "pages" / "index.html").write_text("偽物", encoding="utf-8")
        out = self.tmp / "site"
        out.mkdir()
        (out / "index.html").write_text("生成したページ", encoding="utf-8")
        got = self.site.copy_pages(out, self.tmp / "pages")
        self.assertIn("yachin/", got)
        self.assertIn("unei.html", got)
        self.assertNotIn("index.html", got)
        self.assertEqual((out / "index.html").read_text(encoding="utf-8"), "生成したページ")
        self.assertTrue((out / "yachin" / "index.html").exists())

    def test_no_external_loads(self):
        """ページを開いただけで外のサーバーに接続するもの（フォント・スクリプト・CSS・画像）がない。"""
        bad = re.compile(r'<(?:script|link|img|iframe)[^>]+(?:src|href)="(?:https?:)?//|@import|url\((?:["\']?)(?:https?:)?//', re.I)
        files = list((ROOT / "pages").rglob("*.html")) + list((ROOT / "assets").glob("*.css"))
        self.assertTrue(files)
        for f in files:
            text = f.read_text(encoding="utf-8")
            hits = [m.group(0) for m in bad.finditer(text) if 'rel="canonical"' not in text[max(0, m.start() - 5):m.end() + 40]]
            self.assertEqual(hits, [], f.name)
        css = (ROOT / "assets" / "fonts.css").read_text(encoding="utf-8")
        for name in re.findall(r'url\("fonts/([^"]+)"\)', css):
            self.assertTrue((ROOT / "assets" / "fonts" / name).exists(), name)
        self.assertTrue((ROOT / "assets" / "fonts" / "OFL.txt").exists())

    def test_generated_pages_load_nothing_external(self):
        out = self.tmp / "site"
        self.site.build(out)
        for f in out.rglob("*.html"):
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("fonts.googleapis.com", text, f.name)
            self.assertNotRegex(text, r'<script[^>]+src="https?://', f.name)


if __name__ == "__main__":
    unittest.main()

class TestSearch(unittest.TestCase):
    """検索エンジン向け：正規URL・サイトマップ・Search Console の所有確認。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = build.Site(ROOT / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_canonical_and_sitemap(self):
        # 確認用の設定が空のときの動きも確かめるため、ここでは空にして作る
        self.site.cfg = dict(self.site.cfg, google_site_verification="", google_site_verification_file="")
        out = self.tmp / "site"
        self.site.build(out)
        base = self.site.cfg["base_url"]
        index = (out / "index.html").read_text(encoding="utf-8")
        # トップの正規URLは index.html を付けない形
        self.assertIn(f'<link rel="canonical" href="{base}">', index)
        self.assertNotIn("noindex", index)
        sm = (out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn(f"<loc>{base}</loc>", sm)
        self.assertNotIn("index.html</loc>", sm)
        # サイトマップの URL はすべて公開先の下で、各ページの canonical と一致する
        locs = re.findall(r"<loc>([^<]+)</loc>", sm)
        self.assertTrue(locs)
        for loc in locs:
            self.assertTrue(loc.startswith(base), loc)
            rel = loc[len(base):]
            f = out / (rel + "index.html" if rel == "" or rel.endswith("/") else rel)
            self.assertIn(f'<link rel="canonical" href="{loc}">', f.read_text(encoding="utf-8"), rel)
        # lastmod は日付の形だけ。毎回の生成日を一律に付けない
        for m in re.findall(r"<lastmod>([^<]+)</lastmod>", sm):
            self.assertRegex(m, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(f"<loc>{base}yachin/</loc></url>", sm)
        # 確認用の設定が空なら、確認タグもファイルも出さない
        self.assertNotIn("google-site-verification", index)
        self.assertEqual(list(out.glob("google*.html")), [])

    def test_configured_verification_is_valid(self):
        # いま config/site.json に書いてある値で build が通り、トップにタグが出る
        token = self.site.cfg.get("google_site_verification", "")
        if token:
            out = self.tmp / "site"
            self.site.build(out)
            self.assertIn(f'<meta name="google-site-verification" content="{token}">', (out / "index.html").read_text(encoding="utf-8"))

    def test_verification_meta_and_file(self):
        self.site.cfg = dict(self.site.cfg, google_site_verification="abcDEF_123-xyz456",
                             google_site_verification_file="google0123456789abcdef.html")
        out = self.tmp / "site"
        self.site.build(out)
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="google-site-verification" content="abcDEF_123-xyz456">', index)
        v = out / "google0123456789abcdef.html"
        self.assertEqual(v.read_text(encoding="utf-8"), "google-site-verification: google0123456789abcdef.html")
        self.assertNotIn("google0123456789abcdef", (out / "sitemap.xml").read_text(encoding="utf-8"))

    def test_verification_rejects_whole_tag(self):
        # タグを丸ごと貼ったなど、形が違うときは黙って壊れたページを出さずに止まる
        self.site.cfg = dict(self.site.cfg, google_site_verification='<meta name="google-site-verification" content="x">')
        with self.assertRaises(ValueError):
            self.site.build(self.tmp / "site")
        self.site.cfg = dict(self.site.cfg, google_site_verification="", google_site_verification_file="../evil.html")
        with self.assertRaises(ValueError):
            self.site.build(self.tmp / "site2")


    def test_guides_near_top_of_index(self):
        # 家賃編と支える人へのページは、新着の一覧より前（分野ボタンより上）からもリンクする
        out = self.tmp / "site"
        self.site.build(out)
        index = (out / "index.html").read_text(encoding="utf-8")
        base = self.site.cfg["base_url"]
        g = index.index('id="guides-h"')
        self.assertLess(g, index.index('class="chips"'))
        for path in ("yachin/", "sasaeru/"):
            self.assertIn(f'href="{base}{path}"', index[g:index.index("</section>", g)])

    @unittest.skipUnless(shutil.which("node"), "node がない環境では家賃編の本文の書き込みを確かめない")
    def test_yachin_body_readable_without_script(self):
        # 家賃編の大事な本文（今日やること・手順・制度の候補）が、プログラムを動かさなくても HTML に入っている
        out = self.tmp / "site"
        self.site.build(out)
        y = (out / "yachin" / "index.html").read_text(encoding="utf-8")
        src = (ROOT / "pages" / "yachin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div id="firstOut" class="stack" aria-live="polite"></div>', src)  # 正本は書き換えない
        first = y[y.index('id="firstOut"'):y.index('id="city"')]
        self.assertIn("自立相談支援機関", first)
        self.assertIn("家賃のことで相談したくて電話しました", first)
        self.assertNotIn("閉まっている時間", first)  # 開いた時刻で変わる文は固定しない
        plan = y[y.index('id="planOut"'):y.index('id="candidates"')]
        self.assertIn("持ち物をそろえる", plan)
        self.assertIn("住居確保給付金", y[y.index('id="candOut"'):y.index('id="exclOut"')])
        self.assertIn("<li>", y[y.index('id="srcList"'):])

    def test_yachin_prerender_failure_keeps_page(self):
        # 書き込みに失敗しても、元の家賃編をそのまま公開する（公開は止めない）
        old = build.PRERENDER
        try:
            build.PRERENDER = self.tmp / "missing.cjs"
            text = "<html><body><div id=\"firstOut\"></div></body></html>"
            self.assertEqual(build.prerender_yachin(text), text)
        finally:
            build.PRERENDER = old


class TestReviewRegression(unittest.TestCase):
    def test_medical_headline_is_findable_in_health(self):
        result = lib.classify('OTC類似薬の保険給付の在り方の見直しについて', RULES)
        self.assertIn('health', result['topics'])

    def test_fetch_time_does_not_advance_on_failed_attempt(self):
        site = build.Site(ROOT / 'data')
        site.store['updated'] = '2030-01-01T00:00:00+09:00'
        site.fresh = '2026-11-13T07:00:00+09:00'
        self.assertIn('2026年11月13日', site.updated_text())
        self.assertNotIn('2030', site.footer(0))

    def test_statistics_not_declared_undecided(self):
        site = build.Site(ROOT / 'data')
        self.assertNotIn('まだ決まっていないことで', site.page_talk())
        self.assertIn('調査結果', site.page_talk())

    def test_current_broad_notice_policy_preserved(self):
        self.assertNotIn('useful_kinds', RULES)


class TestSurvey(unittest.TestCase):
    """感想のアンケート。設定が空なら何も出さず、設定があれば押した人だけが外部のフォームを開く形で出す。"""

    FORM = "https://docs.google.com/forms/d/e/1FAIpQLSdTEST_form-ID/viewform"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = build.Site(ROOT / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def configure(self, **kw):
        sv = dict(self.site.cfg.get("survey") or {}, url=self.FORM, page_entry="entry.1234567")
        sv.update(kw)
        self.site.cfg = dict(self.site.cfg, survey=sv)

    def read(self, out, rel):
        return (out / rel).read_text(encoding="utf-8")

    def test_markers_exist_in_pages(self):
        # 目印が消えると、設定してもアンケートが出なくなる
        self.assertIn("<!--survey:yachin-->", (ROOT / "pages" / "yachin" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("<!--survey:unei-->", (ROOT / "pages" / "unei.html").read_text(encoding="utf-8"))
        for f in (ROOT / "pages" / "sasaeru").rglob("index.html"):
            self.assertIn("<!--survey:sasaeru-->", f.read_text(encoding="utf-8"), f)

    def test_nothing_shown_when_not_configured(self):
        self.site.cfg = dict(self.site.cfg, survey=dict(self.site.cfg.get("survey") or {}, url=""))
        out = self.tmp / "site"
        self.site.build(out)
        for f in out.rglob("*.html"):
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("<!--survey", text, f.name)
            self.assertNotIn("アンケートに答える", text, f.name)
            self.assertNotIn("docs.google.com/forms", text, f.name)

    def test_shown_with_disclosure_when_configured(self):
        self.configure()
        out = self.tmp / "site"
        self.site.build(out)
        yachin = self.read(out, "yachin/index.html")
        prefill = self.FORM + "?usp=pp_url&amp;entry.1234567=" + build.quote("家賃が払えないとき（家賃編）")
        self.assertIn(f'href="{prefill}" target="_blank" rel="noopener"', yachin)
        self.assertNotIn("<!--survey", yachin)
        # 押す前に、何が送られて何が送られないかを書いている
        self.assertIn("このページで選んだ内容やメモは送られません", yachin)
        self.assertIn('href="../unei.html#kansou"', yachin)
        # 案内はフッターの前、支える人への案内のあと（緊急の案内の途中に割り込まない）
        self.assertLess(yachin.index("隣で支える人へ"), yachin.index("survey-h"))
        self.assertLess(yachin.index("survey-h"), yachin.index("<footer>"))
        # 支える人のページ：本文のあと、フッターの前。緊急の案内より後ろ
        for rel in ("sasaeru/index.html", "sasaeru/denwa/index.html"):
            text = self.read(out, rel)
            self.assertNotIn("<!--survey", text)
            self.assertLess(text.index("今夜泊まる場所がない"), text.index("survey-h"))
            self.assertLess(text.index("survey-h"), text.index("<footer"))
            self.assertIn("unei.html#kansou", text)
        unei = self.read(out, "unei.html")
        self.assertIn('<h2 id="kansou">', unei)
        self.assertIn("メールアドレスは集めない", unei)
        self.assertIn("返事ができません", unei)
        index = self.read(out, "index.html")
        self.assertIn(build.quote("支援の新着"), index)
        self.assertIn('href="unei.html#kansou"', index)
        self.assertIn('href="../unei.html#kansou"', next((out / "t").glob("*.html")).read_text(encoding="utf-8"))
        self.assertIn("unei.html#kansou", self.read(out, "about.html"))
        # ページを開いただけでフォームに接続するもの（埋め込み）は出さない
        for f in out.rglob("*.html"):
            self.assertNotRegex(f.read_text(encoding="utf-8"), r"<iframe", f.name)

    def test_without_page_entry_links_plain_form(self):
        self.configure(page_entry="")
        out = self.tmp / "site"
        self.site.build(out)
        self.assertIn(f'href="{self.FORM}" target="_blank"', self.read(out, "yachin/index.html"))

    def test_rejects_bad_settings(self):
        for bad in ("javascript:alert(1)", "http://docs.google.com/forms/x", 'https://x.example/"><script>', "<iframe src=x>"):
            self.configure(url=bad)
            with self.assertRaises(ValueError, msg=bad):
                self.site.build(self.tmp / "bad")
        self.configure(page_entry="entry.1&x=1")
        with self.assertRaises(ValueError):
            self.site.build(self.tmp / "bad2")


class TestSasaeru(unittest.TestCase):
    """支える人向けの場面別ページ（pages/sasaeru/）。生成元と一致し、検索向けの項目と出典があり、使わない言葉がない。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_sasaeru
        self.m = make_sasaeru
        self.pages = make_sasaeru.render()

    def test_files_match_generator(self):
        for rel, text in self.pages.items():
            f = ROOT / "pages" / "sasaeru" / rel
            self.assertEqual(f.read_text(encoding="utf-8"), text, f"{rel} が古い：python scripts/make_sasaeru.py を実行")

    def test_search_fields_sources_and_words(self):
        base = json.loads((ROOT / "config" / "site.json").read_text(encoding="utf-8"))["base_url"]
        for rel, text in self.pages.items():
            url = base + "sasaeru/" + rel[: -len("index.html")]
            self.assertIn(f'<link rel="canonical" href="{url}">', text)
            self.assertRegex(text, r"<title>[^<]+</title>")
            self.assertRegex(text, r'<meta name="description" content="[^"]{40,}">')
            self.assertIn("出典と確認日", text)
            for w in ("申請代行", "申請サポート", "受給できます", "noindex"):
                self.assertNotIn(w, text, rel)
            # 外のサーバーから読み込むもの（script・画像・外部CSS）を入れない
            self.assertNotRegex(text, r'<(script|img|iframe)\b')
            self.assertNotRegex(text, r'<link rel="stylesheet" href="https?:')
        for s in self.m.SCENES:
            for k in s["srcs"]:
                self.assertIn(k, self.m.SRC)
