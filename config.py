# config.py : .env の設定（チャンネルの ID が空の機能は動かない）
import datetime
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _id(name):
    return int(os.getenv(name) or 0)


def _list(name, default):
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


TOKEN = os.environ["DISCORD_TOKEN"]
GUILD = _id("GUILD_ID")
OWNER = _id("OWNER_ID")  # 入れると、リマインダーや習慣の声かけでメンションする
JST = datetime.timezone(datetime.timedelta(hours=9))
DATA = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Cosense
PROJECT = os.getenv("COSENSE_PROJECT", "YuzhNote")
SID = os.getenv("COSENSE_SID", "")  # 読むのに使う（非公開プロジェクトの検索・重複確認・索引）
# 1 にすると Bot が Cosense に直接書き込む。0 なら「本文入りの作成リンク」を返すだけ（書き込みは自分のブラウザ）
WRITE = os.getenv("COSENSE_WRITE", "0") == "1"
EDITOR = os.getenv("EDITOR_URL", "https://liu-yuzh.github.io/yuzhnote/")
COSENSE_SYNC_HOURS = float(os.getenv("COSENSE_SYNC_HOURS", "6"))

# 論文
INBOX = _id("INBOX_CHANNEL_ID")
PAPER_FORUM = _id("PAPER_FORUM_ID")
PDF_AUTO = os.getenv("PDF_AUTO", "1") == "1"
WATCH = _id("WATCH_CHANNEL_ID")
ARXIV_CATS = _list("ARXIV_CATEGORIES", "cs.CL")
KEYWORDS = [k.lower() for k in _list("ARXIV_KEYWORDS", "")]
WATCH_MAX = int(os.getenv("WATCH_MAX", "10"))
WATCH_HOUR = int(os.getenv("WATCH_HOUR", "8"))

# 毎日のボード（タスクと習慣）・リマインダー・家計簿・検索
TODAY = _id("TODAY_CHANNEL_ID")
DAY_START_HOUR = int(os.getenv("DAY_START_HOUR", "4"))  # この時刻で日付が切り替わる
HABIT_NUDGE_HOUR = int(os.getenv("HABIT_NUDGE_HOUR", "21"))
REMIND = _id("REMIND_CHANNEL_ID")
KAKEIBO = _id("KAKEIBO_CHANNEL_ID")
CATEGORIES = _list("KAKEIBO_CATEGORIES", "食費,日用品,交通,書籍・研究,趣味,医療,その他")
OCR_LANG = os.getenv("OCR_LANG", "jpn+eng")
SEARCH = _id("SEARCH_CHANNEL_ID")
KINDLE = _id("KINDLE_CHANNEL_ID")
KINDLE_HOUR = int(os.getenv("KINDLE_HOUR", "7"))  # セールは 0 時に切り替わる


def now():
    return datetime.datetime.now(JST)


def today():
    """DAY_START_HOUR 時までは前の日として扱う（夜更かしの日の分を翌朝につけられるように）"""
    return (now() - datetime.timedelta(hours=DAY_START_HOUR)).date()
