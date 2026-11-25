"""一時的な計測（マージ前に削除する）。本番の状態ファイルは書き換えない。"""
import urllib.parse
import copy, hashlib, json, os, re, shutil, sys, tempfile, time, urllib.request
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import watch, lib  # noqa
from watch_extract import extract_html

tmp = Path(tempfile.mkdtemp())
cfg_path = ROOT / "config" / "watch.json"
st = tmp / "state.json"; shutil.copy(ROOT / "data" / "watch_state.json", st)

def summarize(ch):
    return Counter(f"{c['type']}:{c.get('change') or c.get('reason') or ''}" for c in ch)

mode = sys.argv[1]
if mode == "live":
    t0 = time.time()
    a = watch.run(cfg_path, st, tmp / "a.md")
    print("RUN_A(migration)", time.time() - t0, dict(summarize(a)))
    for c in a: print("  A", c["type"], c.get("change") or c.get("reason"), c["url"], c.get("error", ""), c.get("added_count", ""), c.get("removed", ""))
    if (tmp / "a.md").exists(): print("A_REPORT_CHARS", len((tmp / "a.md").read_text())); print((tmp / "a.md").read_text()[:6000])
    b = watch.run(cfg_path, st, tmp / "b.md")
    print("RUN_B(immediate rerun)", dict(summarize(b)))
    for c in b: print("  B", c["type"], c.get("change") or c.get("reason"), c["url"], c.get("zones", ""), c.get("added_count", ""), c.get("removed", ""))
    if (tmp / "b.md").exists(): print((tmp / "b.md").read_text()[:6000])
    s = json.loads(st.read_text())
    for u, v in s["pages"].items():
        sn = v.get("snapshot", {})
        print("  S", sn.get("kind"), sn.get("body_chars"), len(sn.get("blocks", [])), u)
else:  # wayback
    cfg = json.loads(cfg_path.read_text())
    LEG = ("閲覧数", "アクセス数", "ページ番号", r"^\d+$")
    def legacy(text):
        w = lib._Walker(); w.feed(text)
        lines = [s for s in (lib.clean(t["text"]) for t in w.tokens) if len(s) >= 2 and not any(re.search(p, s) for p in LEG)]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()
    def new(text, url, page, strip_chrome=False):
        if strip_chrome:
            text = re.sub(r"(?is)<(header|nav|footer)\b.*?</\1\s*>", "", text)
        r = extract_html(text, url, page, cfg.get("ignore", []))
        return watch.fingerprint([(b["zone"], b["section"], b["hash"]) for b in r["blocks"] if not b["noise"]])
    def get(u):
        req = urllib.request.Request(u, headers={"User-Agent": "shien-news-measure"})
        with urllib.request.urlopen(req, timeout=40) as r: return r.read(), dict(r.headers)
    tot = Counter()
    for page in cfg["pages"]:
        url = page["url"]
        if url.lower().endswith(".pdf"): continue
        try:
            cdx, _ = get("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({"url": url, "output": "json", "from": "2025", "filter": "statuscode:200", "collapse": "timestamp:8"}))
            rows = json.loads(cdx or b"[]")[1:][-6:]
        except Exception as e:
            print("CDX_FAIL", url, str(e)[:80]); continue
        prev = None; res = []
        for row in rows:
            ts = row[1]
            try:
                body, h = get(f"https://web.archive.org/web/{ts}id_/{url}")
                text = lib.decode(body, h)
                cur = (legacy(text), new(text, url, page), new(text, url, page, True))
            except Exception as e:
                res.append(f"{ts}:ERR({str(e)[:60]})"); prev = None; continue
            if prev:
                o, n, nc = (prev[i] != cur[i] for i in range(3))
                tot[f"old={int(o)} new={int(n)} new_wo_chrome={int(nc)}"] += 1
                res.append(f"{ts}:o{int(o)}n{int(n)}c{int(nc)}")
            else:
                res.append(f"{ts}:base")
            prev = cur
            time.sleep(1)
        print("WB", url, " ".join(res))
    print("WB_TOTAL", dict(tot))

if mode == "tokyo":
    from watch_extract import _Tree
    cfg = json.loads(cfg_path.read_text())
    url = sys.argv[2] if len(sys.argv) > 2 else "https://www.fukushi.metro.tokyo.lg.jp/ja/seikatsu/seikatusikinn_fukushi"
    page = next(p for p in cfg["pages"] if p["url"] == url)
    runs = []
    for i in range(4):
        body, hd = lib.fetch(url, retries=1)
        text = lib.decode(body, hd)
        t = _Tree(); t.feed(text); t.close()
        lm = [[(a.tag, a.attrs.get("id", "")) for a in reversed(list(n.ancestors()))] for n in t.nodes
              if not n.skipped and (n.tag in {"main", "article"} or n.attrs.get("role") == "main")]
        r = extract_html(text, url, page, cfg.get("ignore", []))
        runs.append(r)
        print("FETCH", i, len(body), hashlib.sha256(body).hexdigest()[:12], "layout", r["layout"][:12], "blocks", len(r["blocks"]), "chars", r["body_chars"])
        for x in lm: print("   LM", x)
        time.sleep(3)
    base = Counter((b["hash"], b["text"][:150]) for b in runs[0]["blocks"])
    for i, r in enumerate(runs[1:], 1):
        cur = Counter((b["hash"], b["text"][:150]) for b in r["blocks"])
        print("DIFF0-%d" % i, "added", [k[1] for k in (cur - base)][:10], "removed", [k[1] for k in (base - cur)][:10])
