# jptime.py : 「明日9時 ゴミ出し」のような日本語の日時を読む（LLM なし、正規表現だけ）
#   parse(text, now) -> (日時, 繰り返し, 残りの文) / 日時が先頭になければ None
#   繰り返しは "daily" / "weekdays" / "weekly:0,2,4"（0=月曜）/ None
import datetime as dt
import re
import unicodedata

WD = "月火水木金土日"
SEP = r"[\sのに、,]*"
DEFAULT_HOUR = 9
PARTS = {"朝": 8, "昼": 12, "夕方": 17, "夜": 20, "今夜": 20, "今晩": 20}


def _days(spec):
    return sorted({WD.index(c) for c in spec if c in WD})


def _time(d):
    """時刻の正規表現の結果（dict）→ (時, 分)。範囲外なら None"""
    part, h, mi, half = d["part"], d["h"], d["m"] or d["m2"], d["half"]
    if h is None:
        return (PARTS[part], 0) if part in PARTS else None
    h, mi = int(h), int(mi or (30 if half else 0))
    if part in ("午後", "夕方", "夜", "今夜", "今晩") and h < 12:
        h += 12
    return (h, mi) if h < 24 and mi < 60 else None


TIME_RE = (r"(?P<part>午前|午後|朝|昼|夕方|夜|今夜|今晩)?"
           r"(?:(?P<h>\d{1,2})(?::(?P<m>\d{2})|時(?:(?P<m2>\d{1,2})分|(?P<half>半))?))?")


def _match_time(s):
    m = re.match(TIME_RE + SEP, s)
    if not m or not (m.group("part") or m.group("h")):
        return None, s
    t = _time(m.groupdict())
    return (t, s[m.end():]) if t else (None, s)


def next_due(repeat, after, hm):
    """after より後で、繰り返しの条件に合う最初の日時"""
    day = after.date()
    for i in range(8):
        d = day + dt.timedelta(days=i)
        cand = dt.datetime.combine(d, dt.time(*hm), after.tzinfo)
        if cand <= after:
            continue
        if repeat == "daily" or (repeat == "weekdays" and d.weekday() < 5) or \
           (repeat.startswith("weekly:") and d.weekday() in map(int, repeat[7:].split(","))):
            return cand
    raise ValueError(repeat)


def parse(text, now):
    s = unicodedata.normalize("NFKC", text).strip()
    date = repeat = None
    delta = None
    hm = None
    for _ in range(4):  # 「毎週月曜」「明日」「9時」のように並ぶので、何度か頭から読む
        before = s
        if m := re.match(r"毎日" + SEP, s):
            repeat = "daily"
        elif m := re.match(r"平日" + SEP, s):
            repeat = "weekdays"
        # 曜日は「曜」か、空白・数字が続くときだけ（「日記」の「日」を日曜と読まない）
        elif m := re.match(rf"(毎週)?((?:[{WD}][・,]?)+)(?:曜日?|(?=[\s\d:]|$))" + SEP, s):
            days = _days(m.group(2))
            if m.group(1) or len(days) > 1:
                repeat = "weekly:" + ",".join(map(str, days))
            else:
                ahead = (days[0] - now.weekday()) % 7
                date = now.date() + dt.timedelta(days=ahead)
                date = ("weekday", date)
        elif m := re.match(rf"(来週|今週)の?([{WD}])曜日?" + SEP, s):
            monday = now.date() - dt.timedelta(days=now.weekday())
            date = monday + dt.timedelta(days=(7 if m.group(1) == "来週" else 0) + WD.index(m.group(2)))
        elif m := re.match(r"(\d+)(分|時間|日|週間)(半)?後" + SEP, s):
            n = int(m.group(1))
            unit = {"分": "minutes", "時間": "hours", "日": "days", "週間": "weeks"}[m.group(2)]
            delta = dt.timedelta(**{unit: n}) + (dt.timedelta(minutes=30) if m.group(3) and unit == "hours" else dt.timedelta())
        elif m := re.match(r"(今日|きょう|明日|あした|あす|明後日|あさって)" + SEP, s):
            add = {"今日": 0, "きょう": 0, "明日": 1, "あした": 1, "あす": 1, "明後日": 2, "あさって": 2}[m.group(1)]
            date = now.date() + dt.timedelta(days=add)
        elif m := re.match(r"(?:(\d{4})[/年-])?(\d{1,2})[/月-](\d{1,2})日?" + SEP, s):
            y = int(m.group(1) or now.year)
            try:
                date = dt.date(y, int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
            if not m.group(1) and date < now.date():  # 年なしで過ぎた日付は来年
                date = date.replace(year=y + 1)
        else:
            t, rest = _match_time(s)
            if t and hm is None:
                hm, s = t, rest
            if s == before:
                break
            continue
        s = s[m.end():]
    if delta and not (date or repeat or hm):
        return now + delta, None, s.strip()
    if not (date or repeat or hm):
        return None
    if repeat:
        return next_due(repeat, now, hm or (DEFAULT_HOUR, 0)), repeat, s.strip()
    weekday_only = isinstance(date, tuple)
    if weekday_only:
        date = date[1]
    if date is None:  # 時刻だけ：今日、過ぎていれば明日
        cand = dt.datetime.combine(now.date(), dt.time(*hm), now.tzinfo)
        return (cand if cand > now else cand + dt.timedelta(days=1)), None, s.strip()
    cand = dt.datetime.combine(date, dt.time(*(hm or (DEFAULT_HOUR, 0))), now.tzinfo)
    if weekday_only and cand <= now:  # 「金曜」が今日でもう過ぎていたら来週
        cand += dt.timedelta(days=7)
    return cand, None, s.strip()


def fmt(when, now=None):
    return f"{when.month}/{when.day}（{WD[when.weekday()]}）{when:%H:%M}"
