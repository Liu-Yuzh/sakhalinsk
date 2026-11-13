# Sakhalinsk 引き継ぎ資料

Nov 14, 2026 · @toyoyuzh

## 概要

Sakhalinsk は、個人用 Discord サーバーで研究メモと身の回りの管理をする Python 製の Bot。サーバー veritas 上で systemd により常時動いており、主な機能は実装済み。残る大きな課題は、Cosense への直接書き込み（書き込み用の鍵が取れない）だけで、今は「作成リンクを返す方式」で運用している。

方針は次のとおり。

- **LLM は使わない**：トークン費用をかけないため。要約や分類は、正規表現・全文検索・OCR・公開 API で代替する。
- **コマンドを打たせない**：決まったチャンネルに書くだけ、ボタンを押すだけで使える形を優先する。
- **知識の保管先は Cosense（プロジェクト名 YuzhNote、非公開）**。Discord は入口と通知の場所。

関連する既存の資産：

- [Liu-Yuzh/ALdiscordBot](https://github.com/Liu-Yuzh/ALdiscordBot)：以前作った Bot。PDF 抽出、締切リマインダー、確認ボタン、自動再起動の考え方を流用した。
- [Liu-Yuzh/Liu-Yuzh.github.io](https://github.com/Liu-Yuzh/Liu-Yuzh.github.io) の yuzhnote：Cosense 用の userscript 群とメモ送信エディタ。改良はせず、参考と連携先として使う（エディタの `?url=&title=`、`?body=` リンク、`page-io.js` のインポート方式）。

## 環境と配置

Bot はサーバー veritas（ユーザー yuzh）の `~/sakhalinsk` に置き、systemd のユーザーサービス `sakhalinsk` として動かしている。Python は 3.12、仮想環境は `~/sakhalinsk/.venv`。

| 項目 | 内容 |
| --- | --- |
| 置き場所 | `~/sakhalinsk`（`.env`・`.venv`・`bot.db`・`data/` は隠しファイルやデータで、zip には含めない） |
| サービス定義 | `~/.config/systemd/user/sakhalinsk.service`（`WorkingDirectory=%h/sakhalinsk`、`ExecStart=%h/sakhalinsk/.venv/bin/python main.py`、`Restart=on-failure`、`RestartSec=5`） |
| 自動起動 | `loginctl enable-linger` 済みで、ログインしなくても起動時から動く |
| データ | `bot.db`（SQLite）、`data/receipts/年-月/`（レシート写真）、`data/papers/`（論文 PDF） |
| 外部の依存 | `tesseract-ocr`、`tesseract-ocr-jpn`（apt） |
| Python の依存 | discord.py 2.4 以上、aiohttp、python-dotenv、PyMuPDF、pytesseract、Pillow |
| 配布の形 | `bots.zip`（フォルダなしで固めてあり、`unzip -o bots.zip` で上書きできる） |

ファイルの構成：

| ファイル | 役割 |
| --- | --- |
| `main.py` | 起動と cogs の読み込み。Cosense 用に Cookie を覚えない専用の通信セッションを作る |
| `config.py` / `db.py` | `.env` の読み込み ／ SQLite の表をすべて作る |
| `cogs/papers.py` | 論文の登録、フォーラムのスレッド、積読の状態、PDF の抽出 |
| `cogs/arxiv.py` | arXiv の新着通知と📌👎の記録 |
| `cogs/today.py` | 毎日のボード（タスクと習慣） |
| `cogs/remind.py` | リマインダー |
| `cogs/kakeibo.py` | 家計簿 |
| `cogs/kindle.py` | Kindle の日替わりセール |
| `cogs/search.py` | まとめて探す、索引の更新、未整理のリンク |
| `sources.py` | arXiv・Crossref・Semantic Scholar・Web ページからの書誌の取得 |
| `cosense.py` | Cosense の読み書きと、ページ本文の組み立て |
| `jptime.py` | 「明日9時」のような日本語の日時の読み取り |
| `pdftools.py` | PDF から序論の最後・結論・図を取り出す |
| `receipt.py` | レシートの OCR と、金額・日付・店名の読み取り |
| `search_index.py` | 全文検索（SQLite FTS5、trigram） |
| `util.py` | 共通の小さな道具 |
| `test_offline.py` | ネットにつながない部分のテスト（`ok` と出れば正常） |
| `runner.py` | 旧来の自動再起動スクリプト。systemd に置き換え済みで、今は使っていない |

## 実装済みの機能

機能はチャンネルごとに分かれ、`.env` にチャンネルの ID がある機能だけが動く。実際の Discord と Cosense につないで動作を確かめたのは、起動と Cosense の読み込みまで。各機能の中身はオフラインのテストで確認済みだが、スレッドの作成やボタンの反応は、まだ実機で試していない。

| 場所 | 機能 | 使い方 |
| --- | --- | --- |
| `#inbox` | 論文・URL の登録 | URL・arXiv・DOI を貼る。書誌を取り、重複を確かめ、Cosense の作成リンクを返す。論文は `#論文メモ #積読` 付き、Web ページは `#知識メモ` の形 |
| `#inbox`・論文スレッド | PDF の抽出 | PDF を添付すると、序論の最後・結論・図を投稿し、`data/papers/` に保存する |
| 論文フォーラム | 論文ごとのスレッド | 論文を登録すると立つ。arXiv なら PDF を自動で取って抽出する。最初の投稿に積読・読書中・読了のボタン |
| どこでも | `/tsundoku` | 読書中・積読の一覧と、メニューからの状態の変更 |
| arXiv 用 | 新着通知 | 毎朝 `WATCH_HOUR` 時。📌で登録、👎で見送り。反応は `feedback` 表に記録（将来の好みの学習用） |
| `#today` | 毎日のボード | 書いた文がタスクになる（1行1件、`金曜 〇〇` で日付指定）。ボタンで済にし、未完了は翌日へ持ち越す。下のメニューで削除・明日へ・日付変更 |
| `#today` | 習慣トラッカー | `/habit add 名前 日`（毎日・平日・月水金など）。ボードに並び、連続日数を表示。`HABIT_NUDGE_HOUR` 時に声かけ。`/habit stats` で4週間の記録 |
| `#remind` | リマインダー | `明日9時 ゴミ出し`、`毎週月曜8時 資源ごみ` などと書く。時間が来たら完了・10分後・1時間後・明日のボタン。`/remind`、`/reminders` |
| `#kakeibo` | 家計簿 | レシートの写真をサーバーに保存し、Tesseract で金額などを読む。`680 昼食` でも記録できる。分類のボタンは店ごとに覚える。`/kakeibo` で月の集計と CSV |
| `#search` | まとめて探す | 書くとそのまま検索になる（`/find` でも可）。Discord・Cosense・レシート・PDF が対象。`@cosense` などで場所を絞れる。貼ったリンクはページのタイトルも索引に入る |
| どこでも | `/triage` | Discord に貼ったまま Cosense にないリンクを並べ、ボタンで登録 |
| どこでも | `/reindex` | 検索の索引を作り直す（初回に Cosense と Discord の両方を一度） |

ボタンは discord.py の `DynamicItem` で作っており、Bot を再起動しても効く。

## 設定（.env）

`.env` は編集済みで、zip による更新でも上書きされない。値は引用符なしでよいが、空白を含む値（`ARXIV_KEYWORDS` など）は `"…"` で囲む。新しい項目が足りないかは、次で確かめられる。

```
comm -23 <(grep -oE '^[A-Z_]+' .env.example | sort) <(grep -oE '^[A-Z_]+' .env | sort)
```

| 項目 | 意味 | 初期値 |
| --- | --- | --- |
| `DISCORD_TOKEN` | Bot のトークン（必須） | なし |
| `GUILD_ID` | 自分のサーバーの ID。コマンドの即時反映と、過去ログの索引に使う | なし |
| `OWNER_ID` | 自分のユーザー ID。習慣の声かけでメンションする | なし |
| `COSENSE_PROJECT` | Cosense のプロジェクト名。URL の表記と大文字・小文字まで一致させる | YuzhNote |
| `COSENSE_SID` | Cosense にログインした Cookie `connect.sid` の値。`s%3A` から始まる値をそのまま、`.` の後ろの署名まで全部 | なし |
| `COSENSE_WRITE` | 1 で Bot が直接書き込む、0 で作成リンクを返す | 0 |
| `EDITOR_URL` | メモ送信エディタの場所 | https://liu-yuzh.github.io/yuzhnote/ |
| `COSENSE_SYNC_HOURS` | Cosense の索引を更新する間隔（時間） | 6 |
| `INBOX_CHANNEL_ID`、`PAPER_FORUM_ID`、`WATCH_CHANNEL_ID`、`TODAY_CHANNEL_ID`、`REMIND_CHANNEL_ID`、`KAKEIBO_CHANNEL_ID`、`SEARCH_CHANNEL_ID` | 各機能のチャンネル。論文用はフォーラムチャンネル | なし（空の機能は動かない） |
| `PDF_AUTO` | arXiv の論文で PDF を自動で取って抽出する | 1 |
| `ARXIV_CATEGORIES` / `ARXIV_KEYWORDS` | 新着を見る分野 ／ 絞り込むキーワード | cs.CL ／ なし |
| `WATCH_MAX` / `WATCH_HOUR` | 1回に出す最大件数 ／ 通知の時刻 | 10 ／ 8 |
| `DAY_START_HOUR` | この時刻で「今日」が切り替わる | 4 |
| `HABIT_NUDGE_HOUR` | まだの習慣を知らせる時刻 | 21 |
| `KAKEIBO_CATEGORIES` | 家計簿の分類（カンマ区切り） | 食費,日用品,交通,書籍・研究,趣味,医療,その他 |
| `OCR_LANG` / `DATA_DIR` / `DB_PATH` | OCR の言語 ／ 写真や PDF の置き場 ／ データベース | jpn+eng ／ data ／ bot.db |

`COSENSE_SID` は、アカウントにログインしているのと同じ力を持つ。`.env` 以外に置かず、Discord などに貼らない。

## Cosense 連携の現状と未解決の問題

読み込みは動いているが、直接の書き込みは止まっており、今はリンク方式（`COSENSE_WRITE=0`）で運用している。Cosense には公式の書き込み API がなく、Bot はブラウザのやり取りをまねている。

| 操作 | 必要なもの | 状況 |
| --- | --- | --- |
| 読む（検索・重複確認・索引・`/tsundoku`） | `COSENSE_SID`（非公開プロジェクトのため必須） | 動く。Cosense から Yuzhno として認識されている |
| 書く（ページ作成・積読タグの書き換え） | Cookie に加えて CSRF トークン（書き込み用の鍵） | 止まっている。`/api/users/me` が鍵を返さない |
| リンク方式 | なし（書き込みは自分のブラウザ） | 運用中。積読タグだけは手で変える |

これまでに分かったこと：

1. 当初、Bot は他のサイトと同じ通信セッションで Cosense にアクセスしており、返ってきた未ログインの Cookie が混ざるおそれがあった。Cookie を覚えない専用のセッション（`aiohttp.DummyCookieJar`）に直した。
2. それでも書き込みで「Yuzhno としてログインしているが、鍵が返ってこない」となる。Cosense の非公式ライブラリ（scrapbox-userscript-std、2026年11月時点）は今も `/api/users/me` の `csrfToken` を使う前提なので、原因は分かっていない。
3. ブラウザでは鍵がページに `window._csrf` として埋め込まれており、YuzhNote の `page-io.js` もそれを使う。そこで、ページの HTML から鍵を探す `_csrf` 関数を `cosense.py` に足す修正を用意した。ただし、HTML に鍵が本当に入っているかは未確認。

確かめ方（鍵の値そのものは表示しない）：

```
cd ~/sakhalinsk
.venv/bin/python - <<'EOF'
import asyncio, aiohttp, re, config
async def main():
    h = {"Cookie": f"connect.sid={config.SID}"}
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        async with s.get("https://scrapbox.io/api/users/me", headers=h) as r:
            me = await r.json()
        print("users/me の項目:", sorted(me.keys()))
        async with s.get(f"https://scrapbox.io/{config.PROJECT}/", headers=h) as r:
            html = await r.text()
        print("_csrf:", bool(re.search(r"""_csrf\s*=\s*["']([^"']+)""", html)),
              "／ csrfToken:", bool(re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)))
asyncio.run(main())
EOF
```

どちらかが True なら、`_csrf` の修正を当てて `COSENSE_WRITE=1` にすれば直接書き込みに戻せる。どちらも False なら、`users/me の項目` の一覧から鍵の別名を探すか、ブラウザのエディタと同じ WebSocket 経由の書き込みを検討する。

あわせて、Bot 専用の Cosense アカウントを作って YuzhNote に招待し、その Cookie を使う案がある。漏れたときの影響が YuzhNote に限られ、メンバーから外すだけで止められ、Bot が書いた行も見分けられる。Cosense の無料プランは個人利用なら人数制限がない。ただし、これだけでは鍵の問題は直らない。

インポート方式は、同じタイトルのページを丸ごと上書きする点に注意。Bot は作成前にページの有無を確かめるが、積読タグの書き換えでは、読んでから書くまでの数秒にブラウザで編集した分が消えうる。

## 運用手順

普段の操作は systemd のコマンドで行う。コードを変えたら `restart`、ログは `journalctl` で見る。

| したいこと | コマンド |
| --- | --- |
| 起動 ／ 再起動 ／ 停止 | `systemctl --user start sakhalinsk` ／ `restart` ／ `stop` |
| 状態の確認 | `systemctl --user status sakhalinsk`（active (running) なら動いている） |
| ログを流して見る | `journalctl --user -u sakhalinsk -f`（Ctrl+C で抜ける） |
| 手動で試す | 先に `stop` してから `.venv/bin/python main.py`（二重起動を避ける） |
| テスト | `.venv/bin/python test_offline.py` |
| 隠しファイルも含めて一覧 | `tree -a -I .venv` |

zip で更新するとき：

1. `systemctl --user stop sakhalinsk`
2. `unzip -l bots.zip | grep -E '\.venv/|bot\.db|\.env$'` で、残したいものが入っていないか確かめる（何も出なければ安全）
3. `unzip -o bots.zip -d ~/sakhalinsk`（zip にフォルダが含まれるなら `-d ~`）
4. `rm -rf __pycache__`、ブラウザのダウンロードで `_.env.example` のような名前のファイルができていたら消す
5. `.venv/bin/pip install -r requirements.txt`
6. `.env` に足りない項目を確かめて書き足す
7. `test_offline.py` で `ok` を確かめて `start`

初回だけ、Discord で `/reindex` を Cosense と Discord の過去のメッセージの両方について一度ずつ実行する。ログの「Cosense の索引：0 件を更新」は、前回から更新がなければ正常。

よくあるつまずき：

- `. ./.env` で `command not found` が出る：空白を含む値を引用符で囲んでいない。Bot（python-dotenv）は影響を受けない。
- 索引が空のまま：`COSENSE_PROJECT` が URL の表記と違う可能性。`list_pages` の件数を数える確認スクリプトで確かめる。
- 書き込みのエラー：エラー文が「未ログイン」なら `COSENSE_SID` の取り直し、「鍵が見つからない」なら前の節の問題。
- `COSENSE_SID` の取り方：プライベートウィンドウで scrapbox.io にログインし、開発者ツールの Application → Cookies → `https://scrapbox.io` の `connect.sid` の値をコピーし、ログアウトせずに閉じる。

## 未実装のアイデアと既知の制限

候補の中では、右クリックの「Cosense に保存」とボイスチャンネルでの作業時間の記録が、手間に対して効果が大きいと提案済み（まだ返事はない）。どれも LLM なしで作れる。

| 分類 | アイデア | 手段・メモ |
| --- | --- | --- |
| Discord の機能 | 右クリックの「Cosense に保存」（コンテキストメニュー） | 散らばるリンクをその場で登録。`clip` を呼ぶだけ |
| Discord の機能 | ボイスチャンネルの入退室で作業時間を記録 | `on_voice_state_update`。習慣の自動✅にもつなげられる |
| Discord の機能 | ユーザーインストール | 他のサーバーや DM でも `/clip` などを使える |
| Discord の機能 | Bot のステータスに「積読12冊 ／ 習慣2/3」 | `change_presence` |
| Discord の機能 | Components V2 でボードを作り直す | タスクの行ごとにボタンを置ける（discord.py 2.6 以降） |
| Discord の機能 | 投票・サーバーのイベント・Webhook・DM 通知 | 締切をイベントに、機能ごとに送り主の名前を変える など |
| 研究 | 新着論文の好みの学習 | `feedback` 表の📌👎で TF-IDF＋ロジスティック回帰 |
| 研究 | 類似度・関連論文・引用の芋づる | 小型の埋め込みモデル、Semantic Scholar の推薦 API |
| 研究 | 週次の振り返り、穴埋めクイズ、研究日誌と `/report`、学会締切リマインダー | 旧 Bot の締切通知を流用できる |
| 研究 | Zotero・BibTeX、キーワードの自動抽出、抽出型要約 | YAKE、Sudachi＋TF-IDF、sumy |
| 生活 | 価格・在庫の監視、日記 Bot、音声の文字起こし、サーバー監視、処理完了の通知 | faster-whisper、psutil、Webhook |
| 改良 | 旧形式の arXiv ID、URL の正規化（yuzh-dupcheck の urlKey）、`/tsundoku` の件数上限、`og:title` の別の書き方 | コードに `shortcut:` として印がある |

既知の制限：

- PDF の抽出は見出しとキャプションの書式からの推測で、取れない論文がある。
- レシートの OCR は、かすれ・しわ・斜めの写真に弱い。合計の行が読めないときは、お預り・お釣りを除いた最大の金額を使う。
- 検索は部分一致。2文字以下の語は全体をなめるので、量が増えると遅くなる。
- `/triage` は Cosense の索引と照合するため、登録直後のリンクは次の索引更新まで出ることがある。
- Cosense 側で手でタグを変えても、フォーラムのタグとボタンの色は変わらない。
- `yuzh-readstatus` は `#読書メモ` だけが対象で、`#論文メモ` の状態は Cosense 側では切り替えられない。

## 作業の進め方の約束

会話の中で決まった、次の担当者が守るべきこと。

- **コードの修正は「修正前」と「修正後」の2つのブロックで示す。** `-` と `+` の差分表示は使わない。どのファイルの、どの関数の、何行目あたりかを添える。
- **変わったファイルだけを示す。** zip 全体の作り直しは、大きな改修のときだけ。
- **LLM は使わない。** トークンを使う方式は提案しない。どうしても生成が要るなら、ローカル LLM は「保留」の扱い。
- **コマンドを打たせない形を優先する。** チャンネルに書くだけ、ボタンを押すだけ。
- **確認できていないことは、そう書く。** 特に Cosense の非公式 API と、実機の Discord での動作。
- **秘密の値を表示させない。** 確認用のスクリプトでも、`COSENSE_SID` や鍵は「あるかどうか」だけを出す。
- 回答は日本語。

## 次にやること

最優先は、リンク方式への切り替えをサーバーに反映できているかの確認。直近の2つの修正は、修正前・修正後のブロックで渡しただけで、サーバーに当てたかは分かっていない。

- [ ] リンク方式の修正（`config.py`・`cosense.py`・`main.py`・`cogs/papers.py`、`.env` に `COSENSE_WRITE=0`）が当たっているか確かめ、再起動する
- [ ] `#inbox` に URL を貼り、作成リンクが返ってくるかを確かめる
- [ ] 論文フォーラム・ボード・リマインダー・家計簿・検索を、実機で一通り試す（スレッドの作成、ボタン、OCR）
- [ ] `/reindex` を Cosense と Discord で一度ずつ実行し、`/find @cosense` で索引を確かめる
- [ ] Cosense の鍵の確認スクリプトを実行し、HTML から鍵が取れるかを確かめる（取れれば `_csrf` の修正と `COSENSE_WRITE=1` で直接書き込みに戻す）
- [ ] Bot 専用の Cosense アカウントを作るか決める
- [ ] 次の機能（右クリックの「Cosense に保存」、作業時間の記録など）を選ぶ
