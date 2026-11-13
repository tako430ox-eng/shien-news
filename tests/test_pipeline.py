"""取得・解析・分類・サイト生成・変更監視のテスト。ネットワークは使わず、手元の見本ページを小さなサーバーで配る。

実行: python -m unittest discover -s tests -v
"""
import http.server
import json
import os
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
        self.assertEqual(len(items), 3)  # 年別一覧へのリンクは除外
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
        self.assertIn("生活オリエンテーション動画", index)
        # 2回続けて失敗した情報源は画面に警告を出す
        self.assertIn("デジタル庁の新着が、いま自動で取れていません", index)
        # 分野ページ・フィード・サイトマップ
        self.assertTrue((out / "t" / "housing.html").exists())
        self.assertIn("<entry>", (out / "feed.xml").read_text(encoding="utf-8"))
        self.assertIn("t/housing.html", (out / "sitemap.xml").read_text(encoding="utf-8"))
        # 鮮度の基準は「最後に実際に取れた時刻」
        self.assertIn(f'data-generated="{site.fresh}"', index)
        self.assertTrue(site.fresh)

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


if __name__ == "__main__":
    unittest.main()
