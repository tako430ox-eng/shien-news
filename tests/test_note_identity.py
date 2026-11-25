"""同じ公式URLを使う別告知へ、人の説明や期限を誤って引き継がない。"""
from contextlib import redirect_stderr
from datetime import date, datetime
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import lib  # noqa: E402
import update  # noqa: E402


class TestNoteIdentity(unittest.TestCase):
    URL = "https://www.digital.go.jp/policies/health"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def item(self, iid, title="医療費助成について", **kwargs):
        return {"id": iid, "source": "digital", "source_name": "デジタル庁", "title": title,
                "url": self.URL, "published": "2026-11-24", "category": "",
                "first_seen": "2026-11-24T08:00:00+09:00", "last_seen": "2026-11-24T08:00:00+09:00",
                "publish": "auto", "topics": ["health"], "kind": "notice", "excluded": False,
                "business": False, **kwargs}

    def note(self, text="確認した説明です。", **kwargs):
        return {"url": self.URL, "text": text, "checked": "2026-11-25", "reviewed_by": "確認者", **kwargs}

    def write(self, name, data):
        lib.save_json(self.data / name, data)

    def site(self, items, notes, **metadata):
        self.write("items.json", {"updated": "2026-11-25T08:00:00+09:00", "items": items, **metadata})
        self.write("notes.json", {"notes": notes})
        self.errors = io.StringIO()
        with redirect_stderr(self.errors):
            return build.Site(self.data)

    def test_explicit_id_attaches_only_to_its_notice_even_on_shared_url(self):
        medical = self.item("medical")
        pia = self.item("pia", "PIAひな形を掲載しました")
        site = self.site([medical, pia], [self.note(item_id="medical")],
                         shared_urls=[self.URL], legacy_items=[self.item("old")])
        self.assertIn("確認した説明です。", site.card(medical, 0))
        self.assertNotIn("確認した説明です。", site.card(pia, 0))
        self.assertEqual(self.errors.getvalue(), "")

    def test_separate_explicit_notes_for_same_url_are_not_duplicates(self):
        first, second = self.item("one"), self.item("two")
        site = self.site([first, second], [self.note("一つ目の説明", item_id="one"),
                                          self.note("二つ目の説明", item_id="two")])
        self.assertIn("一つ目の説明", site.card(first, 0))
        self.assertNotIn("二つ目の説明", site.card(first, 0))
        self.assertIn("二つ目の説明", site.card(second, 0))

    def test_legacy_url_note_is_compatible_when_unambiguous(self):
        it = self.item("one")
        site = self.site([it], [self.note()])
        self.assertIn("確認した説明です。", site.card(it, 0))
        self.assertEqual(self.errors.getvalue(), "")

    def test_excluded_sibling_still_makes_url_note_ambiguous(self):
        it = self.item("medical")
        hidden = self.item("other", "別のお知らせ", excluded=True, topics=[])
        site = self.site([it, hidden], [self.note()])
        self.assertEqual(len(site.items), 1)
        self.assertNotIn("確認した説明です。", site.card(it, 0))
        self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_url_note_does_not_reactivate_after_sibling_expires(self):
        it = self.item("remaining")
        site = self.site([it], [self.note()], shared_urls=[self.URL])
        self.assertNotIn("確認した説明です。", site.card(it, 0))
        self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_archived_legacy_url_prevents_ambiguous_note_attachment(self):
        it = self.item("recovered")
        site = self.site([it], [self.note()], legacy_items=[self.item("legacy")])
        self.assertNotIn("確認した説明です。", site.card(it, 0))
        self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_fragment_variants_are_shared_for_legacy_note_matching(self):
        it = self.item("one", url=self.URL + "#medical")
        other = self.item("two", url=self.URL + "#pia", excluded=True)
        cases = [([it, other], {}),
                 ([it], {"shared_urls": [self.URL]}),
                 ([it], {"shared_urls": [self.URL + "#past"]}),
                 ([it], {"legacy_items": [self.item("old", url=self.URL + "#old")]})]
        for items, metadata in cases:
            with self.subTest(metadata=metadata, count=len(items)):
                site = self.site(items, [self.note(url=self.URL + "#medical")], **metadata)
                self.assertNotIn("確認した説明です。", site.card(it, 0))
                self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_explicit_note_still_requires_exact_url_including_fragment(self):
        it = self.item("one", url=self.URL + "#medical")
        site = self.site([it], [self.note(item_id="one", url=self.URL + "#pia")])
        self.assertNotIn("確認した説明です。", site.card(it, 0))
        self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_explicit_id_has_priority_over_url_note(self):
        it = self.item("one")
        site = self.site([it], [self.note("古いURL説明"), self.note("告知を指定した説明", item_id="one")])
        card = site.card(it, 0)
        self.assertIn("告知を指定した説明", card)
        self.assertNotIn("古いURL説明", card)

    def test_wrong_or_missing_explicit_target_never_falls_back_by_url(self):
        it = self.item("one")
        cases = [self.note(item_id="missing"),
                 self.note(item_id="one", url="https://www.digital.go.jp/other"),
                 self.note(item_id="")]
        for note in cases:
            with self.subTest(note=note):
                site = self.site([it], [note])
                self.assertNotIn("確認した説明です。", site.card(it, 0))
                self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_invalid_explicit_note_does_not_silently_use_old_url_note(self):
        it = self.item("one")
        site = self.site([it], [self.note("古いURL説明"),
                               self.note(item_id="one", url="https://www.digital.go.jp/other")])
        self.assertNotIn("古いURL説明", site.card(it, 0))

    def test_invalid_explicit_id_blocks_legacy_note_on_same_url(self):
        it = self.item("one")
        for invalid in ("", "missing", None, 123):
            for fragment in ("", "#section"):
                with self.subTest(invalid=invalid, fragment=fragment):
                    site = self.site([it], [self.note("古いURL説明"),
                                           self.note(item_id=invalid, url=self.URL + fragment)])
                    self.assertNotIn("古いURL説明", site.card(it, 0))
                    self.assertNotIn("確認した説明です。", site.card(it, 0))
                    self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_duplicate_explicit_or_legacy_notes_are_withheld_in_both_orders(self):
        it = self.item("one")
        for target in ({}, {"item_id": "one"}):
            notes = [self.note("先の説明", **target), self.note("後の説明", **target)]
            for ordered in (notes, list(reversed(notes))):
                with self.subTest(target=target, first=ordered[0]["text"]):
                    site = self.site([it], ordered)
                    card = site.card(it, 0)
                    self.assertNotIn("先の説明", card)
                    self.assertNotIn("後の説明", card)
                    self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_duplicate_saved_item_ids_cannot_receive_a_note(self):
        first = self.item("duplicate")
        second = self.item("duplicate", url="https://www.digital.go.jp/other")
        for note in (self.note(), self.note(item_id="duplicate")):
            with self.subTest(note=note):
                site = self.site([first, second], [note])
                self.assertNotIn("確認した説明です。", site.card(first, 0))
                self.assertIn("やさしい説明を保留", self.errors.getvalue())

    def test_migration_does_not_modify_notes_or_independent_deadlines(self):
        old = self.item(lib.item_id(self.URL), title_history=["PIAひな形を掲載しました"])
        self.site([old], [self.note()])
        deadlines = {"deadlines": [
            {"title": "人が確認した医療費助成の手続き", "who": "対象の人", "deadline": "2026-12-28",
             "url": self.URL, "source_name": "デジタル庁", "checked": "2026-11-20"},
            {"title": "終了済みの別の手続き", "who": "対象の人", "deadline": "2026-11-01",
             "url": self.URL, "source_name": "デジタル庁", "checked": "2026-10-20"}]}
        self.write("deadlines.json", deadlines)
        self.write("sources.json", {"sources": [{"id": "digital", "name": "デジタル庁", "type": "rss",
                                               "url": "https://example.test/news.xml", "publish": "auto"}]})
        before = {name: (self.data / name).read_bytes() for name in ("notes.json", "deadlines.json")}
        fetched = [{"title": "医療費助成について", "url": self.URL, "date": "2026-11-24", "category": ""},
                   {"title": "PIAひな形を掲載しました", "url": self.URL, "date": "2026-11-24", "category": ""}]
        clock = datetime(2026, 11, 25, 8, tzinfo=lib.JST)
        with patch.object(update, "collect", return_value=fetched), patch.object(update.time, "sleep"), \
                patch.object(update, "now_jst", return_value=clock):
            update.run(self.data / "sources.json", self.data, today=date(2026, 11, 25))
        with redirect_stderr(io.StringIO()), patch.object(build, "now_jst", return_value=clock):
            site = build.Site(self.data)
        html = site.deadlines_html()
        self.assertEqual(site.deadlines, deadlines["deadlines"])
        self.assertEqual(html.count("人が確認した医療費助成の手続き"), 1)
        self.assertIn("2026年11月20日", html)
        self.assertNotIn("終了済みの別の手続き", html)
        self.assertNotIn("PIA", html)
        for name, content in before.items():
            self.assertEqual((self.data / name).read_bytes(), content, name)


if __name__ == "__main__":
    unittest.main()
