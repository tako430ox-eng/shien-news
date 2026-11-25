"""監視用のHTML抽出。本文は呼出元のメモリだけに置き、保存はハッシュに限る。

セレクタは確認済みの #id / .class / タグ名だけを使う。未指定ページは文書全体を
比較する。周辺欄も捨てず、URLと表示文字が既知の無関係リンクに一致した場合だけ
noise を付ける。判定できない構造は、監視範囲を勝手に狭めず例外にする。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from lib import clean


_SKIP = {"head", "script", "style", "template", "svg"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HEADINGS = {f"h{i}" for i in range(1, 7)}
_BLOCK = _HEADINGS | {
    "html", "body", "main", "article", "aside", "section", "header", "footer", "nav",
    "div", "p", "li", "ul", "ol", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot",
    "tr", "td", "th", "caption", "blockquote", "pre", "address", "details", "summary",
    "form", "fieldset", "legend", "figure", "figcaption", "hr",
}
_ATOMIC = _HEADINGS | {"p", "a", "tr", "caption", "pre", "address", "summary", "legend"}
_OPTIONAL_END = {"html", "head", "body", "p", "li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option"}
_ERROR_HEADING = re.compile(
    r"(?:(?:403|404|503)\s*(?:[-:]\s*)?)?(?:forbidden|access denied|not found|page not found|service unavailable)"
    r"|(?:お探しの)?ページ(?:は|が)(?:見つかりません|存在しません)(?:でした)?[。.!]?"
    r"|(?:ただいま)?メンテナンス中です[。.!]?", re.I)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: _Node | None = None
    children: list = field(default_factory=list)
    closed: bool = False
    skipped: bool = False

    def ancestors(self):
        node = self
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    """小さな読み取り用ツリー。HTMLのよくある省略終了タグだけを補う。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", closed=True)
        self.stack = [self.root]
        self.nodes = []

    def _implicit(self, tags, barriers=()):
        for i in range(len(self.stack) - 1, 0, -1):
            node = self.stack[i]
            if node.tag in tags:
                node.closed = True
                del self.stack[i:]
                return
            if node.tag in barriers:
                return

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self._implicit({"head"}, {"html"})
        # template等の内部の省略タグが、外の本文のスタックを閉じてはいけない。
        if not self.stack[-1].skipped:
            if tag in _BLOCK:
                self._implicit({"p"}, {"body", "html", "table"})
            if tag == "li":
                self._implicit({"li"}, {"ul", "ol"})
            elif tag in {"dt", "dd"}:
                self._implicit({"dt", "dd"}, {"dl"})
            elif tag == "tr":
                self._implicit({"tr"}, {"table"})
            elif tag in {"td", "th"}:
                self._implicit({"td", "th"}, {"tr", "table"})
            elif tag in _HEADINGS:
                self._implicit(_HEADINGS, {"body", "html", "div", "section"})
            elif tag == "option":
                self._implicit({"option"}, {"select"})
        if len(self.stack) > 200 or len(self.nodes) >= 200_000:
            raise ValueError("HTMLの入れ子または要素数が多すぎます")
        parent = self.stack[-1]
        node = _Node(tag, dict(attrs), parent, closed=tag in _VOID,
                     skipped=parent.skipped or tag in _SKIP)
        parent.children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack[i].closed = True
                for node in self.stack[i + 1:]:
                    if node.tag in _OPTIONAL_END:
                        node.closed = True
                del self.stack[i:]
                return

    def handle_data(self, data):
        # title は通常の内容比較には入れず、soft error の安全確認だけに使う。
        if not self.stack[-1].skipped or self.stack[-1].tag == "title":
            self.stack[-1].children.append(data)

    def close(self):
        super().close()
        for node in self.stack:
            if node.tag in _OPTIONAL_END:
                node.closed = True


def _select(nodes, selector):
    if not isinstance(selector, str) or not re.fullmatch(r"[.#]?[\w-]+", selector):
        raise ValueError("セレクタは #id / .class / タグ名の単独指定だけ使えます")
    if selector.startswith("#"):
        matches = [n for n in nodes if n.attrs.get("id") == selector[1:]]
    elif selector.startswith("."):
        matches = [n for n in nodes if selector[1:] in (n.attrs.get("class") or "").split()]
    else:
        matches = [n for n in nodes if n.tag == selector.lower()]
    if len(matches) != 1:
        raise ValueError(f"セレクタ {selector} の一致数が1ではありません（{len(matches)}件）")
    node = matches[0]
    if node.skipped or not node.closed:
        raise ValueError(f"セレクタ {selector} の内容または終了位置を確認できません")
    return node


def _inside(node, root):
    return any(parent is root for parent in node.ancestors())


def _resources_and_text(items, base_url):
    """文字とリンク等をDOM順に読む。表のセル境界を文字の連結で消さない。"""
    text, resources, anchors = [], [], []

    def walk(item):
        if isinstance(item, str):
            text.append(item)
            return
        if item.skipped:
            return
        tag, attrs = item.tag, item.attrs
        if tag == "br":
            text.append("\n")
        if tag in {"td", "th"}:
            text.append(" | ")
            resources.append((tag, attrs.get("colspan", "1"), attrs.get("rowspan", "1"), attrs.get("headers", ""), attrs.get("scope", "")))
        if tag == "a" and attrs.get("href") is not None:
            target = urljoin(base_url, attrs["href"].strip())
            resources.append(("a", target, clean(attrs.get("title") or ""),
                              clean(attrs.get("aria-label") or "")))
            anchors.append((item, target))
        if tag in {"img", "iframe", "embed", "object", "video", "audio", "source"}:
            key = "data" if tag == "object" else "src"
            target = urljoin(base_url, (attrs.get(key) or "").strip()) if attrs.get(key) else ""
            alt = clean(attrs.get("alt") or attrs.get("title") or "")
            # srcset はカンマを含む data: URL も許すため分割せず、解決基準も比較する。
            srcset = (base_url, attrs["srcset"]) if attrs.get("srcset") else ""
            resources.append((tag, target, alt, srcset, attrs.get("type", "")))
            if alt:
                text.append(alt)
        if tag == "form" and attrs.get("action") is not None:
            resources.append(("form", urljoin(base_url, attrs["action"])))
        if tag in {"input", "button"} and attrs.get("formaction") is not None:
            resources.append(("formaction", urljoin(base_url, attrs["formaction"])))
        if tag == "li" and attrs.get("value") is not None:
            resources.append(("li-value", attrs["value"]))
        for child in item.children:
            walk(child)
        if tag in {"td", "th"}:
            text.append(" | ")

    for item in items:
        walk(item)
    return clean("".join(text)), resources, anchors


def extract_html(html: str, url: str, page: dict, ignore: list[str]) -> dict:
    """比較用ブロックを返す。安全に抽出できないときは ValueError。"""
    tree = _Tree()
    tree.feed(html)
    tree.close()
    for node in tree.nodes:
        if node.tag == "title" and node.parent.tag == "head":
            heading = clean("".join(c for c in node.children if isinstance(c, str)))
        elif node.tag == "h1" and not node.skipped:
            heading, _, _ = _resources_and_text([node], url)
        else:
            continue
        if _ERROR_HEADING.fullmatch(heading):
            raise ValueError("エラーページの見出しを検知しました")
    content = page.get("content_selectors", [])
    peripheral = page.get("peripheral", [])
    if not isinstance(content, list) or not isinstance(peripheral, list):
        raise ValueError("content_selectors と peripheral は配列で指定してください")
    roots = [_select(tree.nodes, s) for s in content]
    aside_roots, known = [], {}
    for spec in peripheral:
        if not isinstance(spec, dict) or not isinstance(spec.get("known_links", []), list):
            raise ValueError("peripheral の設定形式が不正です")
        node = _select(tree.nodes, spec.get("selector"))
        aside_roots.append(node)
        rules = []
        for rule in spec.get("known_links", []):
            if not isinstance(rule, dict) or not isinstance(rule.get("href"), str) or not isinstance(rule.get("text_pattern"), str):
                raise ValueError("known_links には href と text_pattern が必要です")
            parsed = urlsplit(rule["href"])
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("known_links の href は http(s) の完全URLで指定してください")
            rules.append((rule["href"], re.compile(rule["text_pattern"])))
        known[node] = rules
    selected = roots + aside_roots
    for i, a in enumerate(selected):
        if any(_inside(a, b) or _inside(b, a) for b in selected[i + 1:]):
            raise ValueError("本文と周辺欄のセレクタに重複または包含があります")
    if aside_roots and not roots:
        raise ValueError("周辺欄の指定には明示した本文セレクタも必要です")
    pats = [re.compile(p) for p in ignore]
    min_chars = page.get("min_body_chars", 1)
    if not isinstance(min_chars, int) or isinstance(min_chars, bool) or min_chars < 1:
        raise ValueError("min_body_chars は1以上の整数で指定してください")
    required = page.get("required_text", [])
    if not isinstance(required, list):
        raise ValueError("required_text は正規表現の配列で指定してください")
    required = [re.compile(p) for p in required]

    # base href の変更でも相対リンクの行き先の変化として検知する。
    bases = [n.attrs["href"] for n in tree.nodes if n.tag == "base" and n.attrs.get("href")]
    base_url = urljoin(url, bases[0]) if bases else url
    blocks, body_text = [], []
    headings = {}

    def location(node):
        for i, root in enumerate(aside_roots):
            if _inside(node, root):
                return "peripheral", f"peripheral:{i}", root
        if not roots or any(_inside(node, root) for root in roots):
            return "body", "body", None
        return "outer", "outer", None

    def emit(node, items):
        zone, group, aside = location(node)
        meaningful = [item for item in items
                      if (isinstance(item, str) and item.strip()) or
                      (isinstance(item, _Node) and not item.skipped)]
        # 周辺欄の独立した <a> の並びは1件ずつ照合する。pなどの文章は崩さない。
        if (zone == "peripheral" and meaningful and
                all(isinstance(item, _Node) and item.tag == "a" for item in meaningful) and
                not (len(meaningful) == 1 and meaningful[0] is node)):
            for anchor in meaningful:
                emit(anchor, [anchor])
            return
        plain, resources, anchors = _resources_and_text(items, base_url)
        if not plain and not resources:
            return
        if zone == "outer" and any(p.fullmatch(plain) for p in pats):
            return
        levels = headings.setdefault(group, {})
        if node.tag in _HEADINGS:
            level = int(node.tag[1:])
            for previous in list(levels):
                if previous >= level:
                    del levels[previous]
            levels[level] = plain
        section = " / ".join(levels[k] for k in sorted(levels))
        noise = False
        # 文章を含む周辺欄全体ではなく、表示文字まで照合できた単独リンクだけ。
        candidate = node
        if len(meaningful) == 1 and isinstance(meaningful[0], _Node) and meaningful[0].tag == "a":
            candidate = meaningful[0]
        if aside is not None and candidate.tag in {"a", "li"} and len(anchors) == 1 and len(resources) == 1:
            anchor, target = anchors[0]
            label, _, _ = _resources_and_text(anchor.children, base_url)
            def link_shape(item):
                if isinstance(item, str) or item.skipped:
                    return True
                return (item.tag in {"a", "li", "span", "b", "strong", "em", "i", "small", "br", "wbr"}
                        and all(link_shape(child) for child in item.children))
            if (plain == label and all(link_shape(item) for item in items)
                    and not anchor.attrs.get("title") and not anchor.attrs.get("aria-label")):
                noise = any(target == href and pat.fullmatch(label) for href, pat in known[aside])
        list_context = []
        for ancestor in reversed(list(node.ancestors())):
            if ancestor.tag in {"ol", "ul"}:
                list_context.append((ancestor.tag, ancestor.attrs.get("start", "1"),
                                     ancestor.attrs.get("type", ""), "reversed" in ancestor.attrs))
            elif ancestor.tag == "li" and ancestor.attrs.get("value") is not None:
                list_context.append(("li-value", ancestor.attrs["value"]))
        targets = [r[1] for r in resources if r[0] in {"a", "img", "iframe", "object", "embed", "form", "formaction"} and r[1]]
        display = plain + ("（参照先: " + "、".join(targets) + "）" if targets else "")
        blocks.append({"text": display, "section": section, "zone": zone, "noise": bool(noise),
                       "hash": _hash([zone, section, plain, resources, list_context])})
        if zone == "body":
            body_text.append(plain)

    def walk(node):
        if node.skipped:
            return
        # 選択された領域がブロックの内部にあるときは、その境界を優先する。
        has_boundary = any(root is not node and _inside(root, node) for root in selected)
        if node.tag in _ATOMIC and not has_boundary:
            emit(node, [node])
            return
        if node.tag == "form" and node.attrs.get("action") is not None:
            emit(node, [_Node("form", node.attrs, closed=True)])
        pending = []
        for child in node.children:
            if isinstance(child, _Node) and (child.tag in _BLOCK or any(_inside(root, child) for root in selected)):
                if pending:
                    emit(node, pending)
                    pending = []
                walk(child)
            else:
                pending.append(child)
        if pending:
            emit(node, pending)

    walk(tree.root)
    body = "\n".join(body_text)
    body_chars = sum(len(s) for s in body_text)
    if body_chars < min_chars:
        raise ValueError(f"本文が短すぎます（{body_chars}文字、最低{min_chars}文字）")
    if any(not pattern.search(body) for pattern in required):
        raise ValueError("本文の必須見出し・確認語を見つけられません")
    # 未設定ページも全文を保持したまま、意味上の本文領域が消えたことは検知する。
    landmarks = (selected if selected else
                 [n for n in tree.nodes if not n.skipped and
                  (n.tag in {"main", "article"} or n.attrs.get("role") == "main")])
    layout = [[(a.tag, a.attrs.get("id", "")) for a in reversed(list(node.ancestors()))]
              for node in landmarks]
    return {"blocks": blocks, "layout": _hash(layout), "body_chars": body_chars,
            "body_blocks": sum(b["zone"] == "body" for b in blocks)}
