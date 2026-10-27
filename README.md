# 東京 新規指定マップ

関東信越厚生局が毎月公表している「保険医療機関・保険薬局の新規指定一覧」から**東京都分**を自動で取り込み、地図と一覧で見られるようにする仕組みです。GitHub だけで動きます（サーバー不要・無料枠内）。

- 出典: <https://kouseikyoku.mhlw.go.jp/kantoshinetsu/chousa/shitei.html>
- 毎日 9:23（日本時間）にページを確認し、新しい処理月が出ていれば取り込み → サイト更新 → Issue で通知
- 23区／多摩／島しょ、二次保健医療圏（13圏域）、市区町村、医科・歯科・薬局、指定分／遡及指定分、キーワードで絞り込み
- 一覧表に書かれている全項目を表示（整理した項目に加え、原本の列見出しと値をそのまま保持）
- 住所は国土地理院の住所検索 API で地図上の位置に変換（キャッシュするので同じ住所は一度しか問い合わせない）
- 表示中の一覧を CSV で書き出し

## 設置手順（初回のみ・10分ほど）

1. **リポジトリを作る**: GitHub で新規リポジトリを作成（例 `tokyo-shinki-map`）。GitHub Pages を無料で使うには **Public** にします。
2. **ファイルを置く**: このフォルダの中身をすべてアップロードします。
   - ブラウザの「Add file → Upload files」でフォルダごとドラッグする場合、`.github` フォルダ（隠しフォルダ）が落ちることがあります。落ちたら「Add file → Create new file」でファイル名に `.github/workflows/update.yml` と入力し、中身を貼り付けてください。
   - git が使えるなら `git init && git add . && git commit -m init && git push` で確実です。
3. **Pages を有効にする**: Settings → Pages → Build and deployment の Source を **GitHub Actions** にする。
4. **書き込み権限**: Settings → Actions → General → Workflow permissions を **Read and write permissions** にする。
5. **初回の取り込み**: Actions タブ → 「Update data」→「Run workflow」。`force` 欄に `all` と入れて実行すると、ページに載っている過去12か月分をまとめて取り込みます（住所の位置検索に1件1秒ほどかかるので、初回は20〜30分）。
6. 完了すると `https://<ユーザー名>.github.io/<リポジトリ名>/` で見られます。

### 通知を受け取る

新しい処理月を取り込むたびに、件数・医療圏別の内訳・一覧を書いた Issue が作られます。リポジトリ右上の **Watch → All Activity**（または Custom → Issues）にすると、GitHub からメールやアプリ通知が届きます。

## 画面の見方

| 項目 | 内容 |
|---|---|
| 処理月ごとの件数 | 棒をタップするとその月だけ表示。もう一度タップで全期間。PC では Shift/⌘ を押しながらで複数月 |
| 指定分 | その月に新たに指定されたもの（新規開設が中心） |
| 遡及指定分 | 開設者の交代（個人→法人化など）・移転・組織変更で、前の指定日にさかのぼって指定されたもの。実質的には既存の医療機関 |
| 位置の点線の丸 | 住所から番地まで特定できず、市区町村の代表点に置いたもの |
| 診療科 | 原本の略称（例「内　小　アレ」）を正式名に置き換えて表示。原本の表記も詳細に残しています |

## 仕組み

```
厚生局のページ ──(毎日)──> GitHub Actions
                              ├ scraper/update.py   新しい処理月を検出し、Excel(ZIP) と PDF を data/raw/ に保存
                              ├ parse_excel / parse_pdf  見出し語から列を特定して全項目を抽出（Excel 優先、だめなら PDF）
                              ├ regions.py          市区町村・23区/多摩/島しょ・二次保健医療圏を付与
                              ├ geocode.py          国土地理院 API で緯度経度（data/geocode_cache.json に保存）
                              ├ data/records.json   全レコード（サイトが読むデータ）
                              ├ Issue で通知
                              └ site/index.html + data → GitHub Pages
```

- 厚生局のページには直近12か月分しか載りませんが、取り込んだデータはリポジトリに残り続けるので、それより古い月も見られます。
- 原本（`data/raw/<処理月>/`）も保存しているので、解析方法を直したときは `python -m scraper.update --reparse` で通信なしに作り直せます。
- 厚生局のファイル差し替え（`-1` 付きの再掲など）を検出した場合は、その月を取り直します。

## 困ったとき

- **件数が 0 件・項目がずれている**: 厚生局のファイルの書式が想定と違う可能性があります。`data/meta.json` の該当月の `diagnostics` に、検出した列見出しと件数が入っています。`headers` を見れば、どの列がどう読まれたかがわかります。
- **Excel と PDF で件数が違う**: Actions のログに `WARNING Excel/PDF 件数不一致` と出ます。どちらか多い・項目が埋まっている方を採用しています。
- **特定の月だけ取り直したい**: Run workflow の `force` に `2026-09` のように入力。
- **定期実行が止まった**: Public リポジトリは 60 日間更新がないと定期実行が自動停止します。毎月データが入るので通常は止まりませんが、止まったら Actions タブで再有効化してください。

## 手元で動かす

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q tests          # 合成データでのテスト
python -m scraper.update            # 実データ取り込み（data/ が更新される）
python -m http.server               # http://localhost:8000/site/ で表示
```

## 注意

- 位置は住所から自動で求めたもので、ずれることがあります。正確な所在地は原本で確認してください。
- 地図: [地理院タイル](https://maps.gsi.go.jp/development/ichiran.html)（淡色地図）、住所検索: 国土地理院。
- 厚生局のサーバーに負担をかけないよう、1回の実行でのダウンロードは新しい月の分だけ、リクエスト間隔は2秒以上にしています。
