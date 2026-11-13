"""3回以上つづけて取得に失敗している情報源があれば、Issue の本文を標準出力に書く。なければ何も書かない。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import ROOT, load_json  # noqa: E402

health = load_json(ROOT / "data" / "health.json", {"sources": {}})
bad = [h for h in health["sources"].values() if h.get("failures", 0) >= 3]
if bad:
    print("次の情報源から、新着が3回以上つづけて取れていません。サイトにはこの情報源の警告が出ています。\n")
    for h in bad:
        print(f"- {h['name']}（{h.get('url', '')}）：{h.get('failures')}回つづけて失敗。最後のエラー：{h.get('last_error')}")
    print("\nよくある原因：ページの住所（URL）が変わった、ページの作りが変わって読めなくなった（0件）、一時的な障害。")
    print("直し方：config/sources.json の url や item_href を新しいページに合わせ、tests/fixtures に新しい見本を足してテストを通してください。")
    print("直ったら、この Issue を閉じてください。")
