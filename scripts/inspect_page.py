"""ページの作りを調べる（点検用）。文字とリンクの並び、リンクの数、本文の大きさをログに出す。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import _Walker, decode, fetch  # noqa: E402

url = sys.argv[1]
body, headers = fetch(url, retries=0)
html = decode(body, headers)
print("bytes", len(body), "content-type", headers.get("Content-Type"))
print("script tags", len(re.findall(r"<script", html)), "a tags", len(re.findall(r"<a\s", html)))
for m in re.finditer(r"<(main|ul|ol|dl|article|section|li)[^>]*class=\"([^\"]*)\"", html[:200000]):
    pass
w = _Walker()
w.feed(html)
print("tokens", len(w.tokens))
for t in w.tokens[:220]:
    if t["type"] == "a":
        print("A", repr(t.get("href")), "|", " ".join(t["text"].split())[:100])
    else:
        print("T", " ".join(t["text"].split())[:100])
i = html.find("/press/")
print("first /press/ at", i)
if i >= 0:
    print(html[max(0, i - 1500): i + 800])
