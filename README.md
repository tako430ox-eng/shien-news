# くらしの非常口 新着

国の機関の公式ページに出た新着情報のうち、生活・住まい・仕事・障害・子ども・高齢・外国籍の人などに関わりそうなものを、1日2回自動で集めて並べる無料のサイトです。あわせて、行動支援の試作「家賃の次の一歩」が出典にしている公式ページの変化を週1回見張り、変化があれば知らせます。

- 運営の目的：既存の支援制度と、それを必要とする人との間の障壁を小さくすること（プロジェクト「社会的弱者の救済」）。
- 費用：0円（GitHub の公開リポジトリで、GitHub Actions と GitHub Pages を使う）。
- 外部ライブラリ：なし（Python 3.12 の標準ライブラリだけ）。

## しくみ

```
[GitHub Actions 1日2回]
  scripts/update.py   情報源（config/sources.json）を読む → 見出し・URL・掲載日を取り出す
                      → 見出しの言葉で分野と種類を付ける（config/rules.json、AIは使わない）
                      → data/items.json と data/health.json に保存
  scripts/build.py    data/ から静的なサイト（site/）を作る → GitHub Pages に公開
  scripts/health_report.py  3回続けて取れない情報源があれば Issue を作る

[GitHub Actions 週1回]
  scripts/watch.py    config/watch.json のページを読み、前回から変わったら Issue を作る
```

## 守っていること

| 決まり | 理由 | どこで守っているか |
|---|---|---|
| 情報源は国の機関の公式ページだけ。利用条件（公共データ利用規約 第1.0版）を確認したものだけ | 誤った情報・古い情報は困っている人に実害を与える。著作権 | config/sources.json（license, checked） |
| 厚生労働省・内閣府の RSS は使わない | 両省の RSS のページが「その情報に基づいたホームページの作成、情報の再配布」を断っているため | config/sources.json の note |
| 見出しは公式のまま。機械（AIを含む）で要約しない | 要約の誤りを防ぐ | lib.tidy（空白だけ整える）、テスト |
| 分野・種類は見出しの言葉で機械的に付け、「自動の分類」と表示する | 判断の根拠を説明できるようにする | config/rules.json |
| 「検討中（審議会など）」「意見募集」は、まだ決まっていないことを明示する | 決まったと誤解させない | rules.json の hint |
| やさしい説明・締め切りは、人が公式ページで確かめたものだけ、確認日つきで載せる | 自動では正確さを保証できない | data/notes.json、data/deadlines.json |
| 更新が止まったら画面に警告を出す（最後に実際に取れた時刻から72時間） | 古い情報を新しいと思わせない | assets/site.js、build.py の fresh |
| 取れない情報源は画面と Issue で知らせる | 静かに壊れるのを防ぐ | update.py の health、health_report.py |
| 利用者の情報を集めない。解析・広告のプログラムを入れない | 安全と信頼。外部送信規律の対象にしない | build.py |

## 手元で動かす

```
python -m unittest discover -s tests      # テスト（ネットワーク不要）
python scripts/update.py                  # 実際に取得（ネットワークが必要）
python scripts/build.py                   # site/ を作る
python tests/sample/make_sample.py /tmp/d && python scripts/build.py --data /tmp/d --out /tmp/s --sample   # 見本で画面確認
```

## 公開の手順（最初の1回だけ）

1. GitHub で公開（Public）リポジトリを作り、このフォルダの中身を入れる。
2. リポジトリの Settings → Pages → Source を「GitHub Actions」にする。
3. Actions タブで「新着の取得と公開」を選び、Run workflow を押す（以後は自動）。
4. 公開されたURL（`https://<アカウント名>.github.io/<リポジトリ名>/`）を config/site.json の base_url に書く。

詳しい運用は docs/operations.md。
