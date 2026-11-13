"""行動支援の試作（正本）を、公開用の pages/yachin/index.html に写す。見出し用フォントの部分集合も作り直す。

使い方: python scripts/publish_app.py <正本の index.html>
  正本は Artifact 用の断片（<title> から始まり <html> や <head> がない）なので、ここで文書の形に整える。
  - Google Fonts の読み込みを外し、assets/fonts.css（自前で置いたフォント）に替える。
  - 正規のURL、運営者ページと新着へのリンクを足す。
  - フォントは Google Fonts の text= 指定で、ページで使う文字だけの部分集合（woff2）を取ってきて assets/fonts/ に置く。
    この取得はこのスクリプトを動かしたときだけ行い、公開後のページは Google に接続しない。
ネットワークが必要。Actions では動かさない（公開する中身は人が確かめてからコミットする）。
"""
from __future__ import annotations

import hashlib
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://tako430ox-eng.github.io/shien-news/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
WEIGHTS = (700, 900)
FONT_LINK = re.compile(r'<link rel="(?:preconnect|stylesheet)" href="https://fonts\.(?:googleapis|gstatic)\.com[^"]*"[^>]*>\n?')


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def make_page(src: str) -> str:
    sha = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
    body = FONT_LINK.sub("", src)
    body = body.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
    if "fonts.googleapis.com" in body or "fonts.gstatic.com" in body:
        raise SystemExit("Google Fonts の読み込みが残っています。正本の書き方が変わった可能性があります。")
    i = body.index("<style>")
    head, rest = body[:i], body[i:]
    j = rest.index("</style>") + len("</style>")
    style, main = rest[:j], rest[j:]
    canon = BASE + "yachin/"
    m = re.search(r'<meta name="description" content="([^"]*)"', head)
    desc = m.group(1) if m else ""
    t = re.search(r"<title>(.*?)</title>", head)
    title = t.group(1) if t else "くらしの非常口"
    links = ('<p><a href="../unei.html">運営者と連絡先</a> ・ '
             '<a href="../">支援の新着（くらしの非常口 新着）</a></p>\n    ')
    if "<footer>" not in main or "<details>" not in main.split("<footer>", 1)[1]:
        raise SystemExit("フッターの形が想定と違います。運営者ページへのリンクを入れる場所を確かめてください。")
    a, b = main.split("<footer>", 1)
    b = b.replace("<details>", links + "<details>", 1)
    main = a + "<footer>" + b
    return f"""<!doctype html>
<!-- 正本: /mnt/project-files/action-support/app/index.html（sha256 先頭 {sha}）。scripts/publish_app.py で作った。直接は編集しない。 -->
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{head.strip()}
<link rel="canonical" href="{canon}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canon}">
<link rel="stylesheet" href="../assets/fonts.css">
{style}
</head>
<body>
{main.strip()}
</body>
</html>
"""


def font_text() -> str:
    """見出しフォントで出るかもしれない文字。ページと設定に出てくる文字と、英数字・日付の文字。"""
    files = [ROOT / "pages" / "yachin" / "index.html", ROOT / "pages" / "unei.html", ROOT / "scripts" / "build.py"]
    files += sorted((ROOT / "config").glob("*.json"))
    chars = set("".join(chr(c) for c in range(0x20, 0x7F)))
    chars |= set("０１２３４５６７８９年月日曜（）、。・「」→←↓↑？！")
    for f in files:
        chars |= {c for c in f.read_text(encoding="utf-8") if ord(c) > 0x7F and not c.isspace()}
    return "".join(sorted(chars))


CHUNK = 300  # text= が長すぎると Google が黙って壊れたフォントを返すので、分けて取る


def urange(chars: str) -> str:
    return ", ".join("U+%x" % ord(c) for c in sorted(chars))


def make_fonts() -> None:
    text = font_text()
    out = ROOT / "assets" / "fonts"
    if out.exists():
        for old in out.glob("*.woff2"):
            old.unlink()
    out.mkdir(parents=True, exist_ok=True)
    faces = []
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
    for w in WEIGHTS:
        for n, part in enumerate(chunks, 1):
            url = ("https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@%d&text=%s" % (w, urllib.parse.quote(part)))
            css = get(url).decode("utf-8")
            m = re.search(r"src: url\((https://fonts\.gstatic\.com/[^)]+)\) format\('woff2'\)", css)
            if not m:
                raise SystemExit("フォントの取得に失敗しました（woff2 のURLが見つからない）。")
            data = get(m.group(1))
            if len(data) < 20 * len(part.strip()):
                raise SystemExit(f"フォントが小さすぎます（{w} の {n} 番目、{len(data)} バイト）。取得に失敗した可能性があります。")
            name = f"zen-maru-gothic-{w}-{n}.woff2"
            (out / name).write_bytes(data)
            faces.append(f"""@font-face {{
  font-family: "Zen Maru Gothic";
  font-style: normal;
  font-weight: {w};
  font-display: swap;
  src: url("fonts/{name}") format("woff2");
  unicode-range: {urange(part)};
}}""")
    (ROOT / "assets" / "fonts.css").write_text(
        "/* 見出し用フォント Zen Maru Gothic（SIL Open Font License 1.1。fonts/OFL.txt）。\n"
        "   ページで使う文字だけを含む部分集合。scripts/publish_app.py で作り直す。\n"
        "   含まれない文字は、tokens.css の --f-display に並べた端末のフォントで表示される。 */\n"
        + "\n".join(faces) + "\n", encoding="utf-8")
    total = sum(p.stat().st_size for p in out.glob("*.woff2"))
    print(f"fonts: {len(text)} 文字, {len(faces)} ファイル, 合計 {total // 1024}KB")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1]).read_text(encoding="utf-8")
    dst = ROOT / "pages" / "yachin" / "index.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(make_page(src), encoding="utf-8")
    print("wrote", dst)
    make_fonts()


if __name__ == "__main__":
    main()
