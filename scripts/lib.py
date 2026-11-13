"""共通の部品：取得・解析・分類。外部ライブラリを使わない（標準ライブラリだけ）。"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
USER_AGENT = "shien-news-bot/1.0 (+non-profit; daily check of public notices)"


def now_jst() -> datetime:
    return datetime.now(JST)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------- 取得

def fetch(url: str, timeout: int = 30, retries: int = 2) -> tuple[bytes, dict]:
    """URLを取得する。失敗したら少し待って再試行し、最後は例外を投げる。"""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return body, dict(r.headers)
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code not in (408, 429):
                break  # 見つからない・禁止などは、待っても変わらないので再試行しない
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # noqa: PERF203
            last = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"取得できませんでした: {url}: {last}")


def decode(body: bytes, headers: dict | None = None) -> str:
    charset = None
    ctype = (headers or {}).get("Content-Type") or (headers or {}).get("content-type") or ""
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:3000], re.I) or re.search(rb"encoding=[\"']([\w-]+)", body[:200])
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in [charset, "utf-8", "cp932", "euc-jp"]:
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- 文字の整え

def clean(s: str) -> str:
    """比べるための正規化（全角英数を半角に、空白をまとめる）。表示には使わない。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tidy(s: str) -> str:
    """表示用。公式の見出しの文字は変えず、空白（全角空白は残す）と改行だけを整える。"""
    s = re.sub(r"[ \t\r\n\u00a0]+", " ", s or "").strip()
    return s


_ERA = {"令和": 2018, "平成": 1988}
_DATE_RE = re.compile(r"(?:(\d{4})|(令和|平成)(\d{1,2}|元))\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?")


def find_date(text: str) -> date | None:
    """文中の最初の日付を返す（2026年11月12日、令和8年11月12日、2026/11/12）。"""
    t = clean(text)
    m = _DATE_RE.search(t)
    if not m:
        return None
    if m.group(1):
        y = int(m.group(1))
    else:
        n = 1 if m.group(3) == "元" else int(m.group(3))
        y = _ERA[m.group(2)] + n
    try:
        return date(y, int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- HTML の一覧ページ

class _Walker(HTMLParser):
    """HTMLを「文字」と「リンク」の並びに変える。script/style/nav/header/footer の中は読まない。"""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict] = []
        self._skip = 0
        self._a: dict | None = None
        self._skip_tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        # リンクの中の header（こども家庭庁のカードの見出し部分）は読む。ページ全体の header だけ読まない
        if tag in self.SKIP and not (tag in ("header", "footer") and self._a is not None):
            self._skip += 1
            self._skip_tags.append(tag)
            return
        if self._skip:
            return
        if tag == "a":
            href = dict(attrs).get("href")
            self._a = {"type": "a", "href": href, "text": ""}
        elif tag in ("br", "p", "li", "dt", "dd", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "span", "time"):
            if self._a is not None:
                self._a["text"] += " "

    def handle_endtag(self, tag):
        if self._skip_tags and tag == self._skip_tags[-1]:
            self._skip_tags.pop()
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._a is not None:
            self.tokens.append(self._a)
            self._a = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._a is not None:
            self._a["text"] += data
        elif data.strip():
            self.tokens.append({"type": "text", "text": data})


def parse_html_list(html: str, base_url: str, item_href: str, strip_prefixes=(), exclude_href: str | None = None, require_date: bool = True) -> list[dict]:
    """新着一覧ページから {title, url, date, category} を取り出す。

    日付の見つけ方：リンクの文字の中に日付があればそれを使う（こども家庭庁型）。
    なければ、そのリンクより前に最後に出てきた日付を使う（厚生労働省の「日ごとの見出し」型、
    出入国在留管理庁の「日付の行のあとにリンク」型）。
    """
    w = _Walker()
    w.feed(html)
    href_re = re.compile(item_href)
    ex_re = re.compile(exclude_href) if exclude_href else None
    last_date = None
    last_text = ""
    seen = set()
    out = []
    for tok in w.tokens:
        if tok["type"] == "text":
            d = find_date(tok["text"])
            if d:
                last_date = d
                rest = clean(_DATE_RE.sub("", clean(tok["text"])))
                last_text = rest
            else:
                last_text = clean(tok["text"])
            continue
        href = tok.get("href")
        if not href:
            continue
        url = urljoin(base_url, href.strip())
        url = url.split("#")[0]
        if not href_re.search(url) or (ex_re and ex_re.search(url)):
            continue
        text = tidy(tok["text"])
        # こども家庭庁型：リンクの文字の「最後」にある日付が掲載日（見出しの途中の日付は催しの日付など）
        m_end = re.search(r"((?:\d{4}|[０-９]{4}|令和\s*\d{1,2}|平成\s*\d{1,2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*$", text)
        d_in = find_date(m_end.group(1)) if m_end else None
        if d_in:
            text = tidy(text[: m_end.start()])
        category = ""
        changed = True
        while changed:
            changed = False
            for p in list(strip_prefixes) + ["NEW", "New", "new"]:
                if text.startswith(p + " ") or text == p:
                    category = category or (p if p.upper() != "NEW" else "")
                    text = text[len(p):].strip()
                    changed = True
                if text.endswith(" " + p):
                    text = text[: -len(p)].strip()
                    changed = True
        if not category and last_text and len(last_text) <= 12:
            category = last_text
        d0 = d_in or last_date
        if len(text) < 6 or url in seen or (require_date and d0 is None):
            continue
        seen.add(url)
        d = d_in or last_date
        out.append({"title": text, "url": url, "date": d.isoformat() if d else None, "category": category})
    return out


# ---------------------------------------------------------------- RSS / Atom

def parse_feed(xml_text: str) -> list[dict]:
    """RSS 1.0（RDF）・RSS 2.0・Atom を読む。"""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = link = when = None
        cats = []
        for c in el:
            n = local(c.tag)
            if n == "title":
                title = tidy("".join(c.itertext()))
            elif n == "link":
                link = (c.get("href") or c.text or "").strip() or link
            elif n in ("date", "pubDate", "updated", "published") and not when:
                when = (c.text or "").strip()
            elif n in ("category", "subject"):
                cats.append(clean(c.text or c.get("term") or ""))
        if not title or not link:
            continue
        d = None
        if when:
            try:
                d = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(JST).date()
            except ValueError:
                try:
                    d = parsedate_to_datetime(when).astimezone(JST).date()
                except (TypeError, ValueError):
                    d = find_date(when)
        out.append({"title": title, "url": link, "date": d.isoformat() if d else None, "category": cats[0] if cats else ""})
    return out


# ---------------------------------------------------------------- 分類（規則だけ。AIは使わない）

def _hit(text: str, words) -> list[str]:
    t = clean(text)
    return [w for w in words if clean(w) in t]


def classify(title: str, rules: dict, category: str = "", default_topics=()) -> dict:
    t = clean(title)
    topics = [tp["slug"] for tp in rules["topics"] if _hit(t, tp["keywords"])]
    # 情報源そのものが特定の分野の機関（こども家庭庁、出入国在留管理庁）なら、その分野を必ず付ける
    topics += [d for d in default_topics if d not in topics]
    kind = rules["default_kind"]["id"]
    for k in rules["kinds"]:
        if _hit(t, k["keywords"]):
            kind = k["id"]
            break
    excluded = bool(_hit(t, rules["exclude_keywords"])) or ("採用" in category) or ("調達" in category)
    business = bool(_hit(t, rules["business_keywords"]))
    return {"topics": topics, "kind": kind, "excluded": excluded, "business": business}
