# test_offline.py : ネットに出ない部分の確認（python test_offline.py）
from urllib.parse import parse_qs, unquote, urlparse

import cosense
import sources

FEED = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2401.12345v2</id><published>2024-01-22T00:00:00Z</published>
<title>A Study of
  Notes</title><summary> We study
 notes. </summary>
<author><name>Ada [L.] Lovelace</name></author><author><name>Alan Turing</name></author>
</entry><entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title></entry></feed>"""

papers = sources.parse_arxiv_feed(FEED)
assert len(papers) == 1, "エラーの entry は捨てる"
p = papers[0]
assert p["id"] == "2401.12345" and p["title"] == "A Study of Notes" and p["abstract"] == "We study notes."

assert sources.ARXIV_RE.search("https://arxiv.org/pdf/2401.12345v3").group(1) == "2401.12345"
assert sources.ARXIV_RE.search("arxiv:2401.12345").group(1) == "2401.12345"
assert sources.DOI_RE.search("https://doi.org/10.1145/3290605.3300233 です").group(1) == "10.1145/3290605.3300233"

p["tldr"] = "Notes help."
lines = cosense.paper_lines(p)
assert lines[0] == "#論文メモ #積読"
assert "著者：[Ada L. Lovelace] [Alan Turing]" in lines, "名前の [ ] はリンクを壊すので外す"
assert "発表：2024 [arXiv]" in lines and ">Notes help." in lines

cs = cosense.Cosense(None, "YuzhNote")
url = cs.page_url("A/B テスト", lines)
u = urlparse(url)
assert unquote(u.path) == "/YuzhNote/A/B テスト" and "%2F" in u.path, "タイトルの / はパスに入れない"
assert parse_qs(u.query)["body"][0].split("\n") == lines

w = {"kind": "web", "url": "https://example.com/a?x=1&y=2", "title": "例 & テスト"}
q = parse_qs(urlparse(cosense.editor_url("https://liu-yuzh.github.io/yuzhnote/", w)).query)
assert q == {"url": [w["url"]], "title": [w["title"]]}


# ---------- 日本語の日時 ----------
import datetime as dt
import jptime

J = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 13, 10, 0, tzinfo=J)  # 金曜 10:00


def P(t):
    r = jptime.parse(t, NOW)
    return None if r is None else (r[0].strftime("%m/%d %H:%M"), r[1], r[2])


for k, v in {
    "明日9時 ゴミ出し": ("11/14 09:00", None, "ゴミ出し"), "3時間後 洗濯": ("11/13 13:00", None, "洗濯"),
    "1時間半後 会議": ("11/13 11:30", None, "会議"), "8時 起きる": ("11/14 08:00", None, "起きる"),
    "午後3時半に電話": ("11/13 15:30", None, "電話"), "11月20日の15時 締切": ("11/20 15:00", None, "締切"),
    "2/1 提出": ("02/01 09:00", None, "提出"), "来週の水曜日10:30 面談": ("11/18 10:30", None, "面談"),
    "金曜 振り返り": ("11/20 09:00", None, "振り返り"), "毎日22時 日記": ("11/13 22:00", "daily", "日記"),
    "平日 8時 メール": ("11/16 08:00", "weekdays", "メール"), "月水金 7時 ジム": ("11/16 07:00", "weekly:0,2,4", "ジム"),
    "明日の朝 散歩": ("11/14 08:00", None, "散歩"), "日記を書く": None, "３冊読む": None, "25時 だめ": None,
}.items():
    assert P(k) == v, (k, P(k), v)

# ---------- レシート ----------
import receipt

r = receipt.parse("セブン-イレブン 東京駅前店\nTEL 03-1234-5678\n2026年11月13日(金)\nお茶 ¥140\n小 計 ¥290\n"
                  "合 計 ¥1,290\nお預り ¥2,000\nお釣り ¥710")
assert r == {"amount": 1290, "date": dt.date(2026, 11, 13), "store": "セブン-イレブン 東京駅前店"}, r
assert receipt.parse("ローソン\n26/11/02\n商品 ¥300\nお預り ¥1000")["amount"] == 300  # 合計が読めないとき
for k, v in {"680 昼食": (680, "昼食"), "昼食 680円": (680, "昼食"), "3冊 1200": (1200, "3冊"), "論文を読む": None}.items():
    assert receipt.parse_manual(k) == v, (k, receipt.parse_manual(k))

# ---------- 論文 PDF ----------
import pymupdf
import pdftools

doc = pymupdf.open()
pg = doc.new_page()
for rect, text in [((50, 30, 550, 62), "A Study"), ((50, 70, 550, 90), "1 Introduction"),
                   ((50, 95, 550, 170), "Background text that is long enough. " * 6),
                   ((50, 175, 550, 250), "Our contributions are two: a bot and an evaluation."),
                   ((50, 305, 550, 325), "2 Related Work"), ((50, 330, 550, 400), "Prior work is described here. " * 8),
                   ((100, 565, 500, 600), "Figure 1: Overview.")]:
    assert pg.insert_textbox(pymupdf.Rect(*rect), text, fontsize=9) >= 0, text
pg.draw_rect(pymupdf.Rect(100, 410, 300, 560), fill=(0.8, 0.8, 1))
pg = doc.new_page()
for rect, text in [((50, 40, 550, 60), "5 Conclusion"), ((50, 65, 550, 140), "We presented a bot."),
                   ((50, 150, 550, 170), "References"), ((50, 175, 550, 200), "[1] Someone.")]:
    pg.insert_textbox(pymupdf.Rect(*rect), text, fontsize=9)
intro, concl, figs = pdftools.extract(doc.tobytes())
assert "contributions" in intro and concl == "We presented a bot." and [n for n, _ in figs] == [1], (intro, concl)

# ---------- 積読の状態 ----------
L = ["T", *cosense.paper_lines(p)]
a = cosense.set_status(L, "読了")
assert a[1] == "#論文メモ #読了" and any(x.startswith("読了日：") for x in a) and cosense.set_status(a, "読了") == a

# ---------- 全文検索 ----------
import os
import tempfile

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
import search_index

search_index.put("cosense", "p1", "Discordで研究メモ", "論文を管理する。検索性を上げる", "u1", "2026-11-01")
search_index.put("discord", "1", "#雑談", "🔗 検索エンジンの仕組み", "u2", "2026-11-10")
assert {h["ref"] for h in search_index.search("検索")} == {"p1", "1"}  # 2文字は LIKE で探す
assert [h["ref"] for h in search_index.search("@discord 検索エンジン")] == ["1"]
print("ok")
