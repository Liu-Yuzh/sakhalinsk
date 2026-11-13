# cogs/today.py : 毎日のボード。今日のタスクと習慣をボタンで並べ、押すだけで済ませる
#   ・#today に書いた文がそのままタスクになる（1行1件。「明日 論文読む」のように日付を先頭に書くとその日に出る）
#   ・終わらなかったタスクは翌日のボードに持ち越す
#   ・習慣は /habit で登録。ボードのボタンで記録し、/habit stats で連続日数と4週間の記録を見る
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import jptime
from util import channel

MAX_BUTTONS = 20  # 5行のうち4行をボタン、最後の1行を整理のメニューに使う
DAY_SPECS = {"毎日": "0123456", "平日": "01234", "週末": "56", "土日": "56"}


def iso(d):
    return d.isoformat()


def wd(d):
    return f"{d.month}/{d.day}（{jptime.WD[d.weekday()]}）"


def parse_days(spec):
    spec = (spec or "毎日").strip()
    if spec in DAY_SPECS:
        return DAY_SPECS[spec]
    days = "".join(sorted({str(jptime.WD.index(c)) for c in spec if c in jptime.WD}))
    return days or None


def days_label(days):
    return next((k for k, v in DAY_SPECS.items() if v == days), "・".join(jptime.WD[int(c)] for c in days))


def habits_on(day):
    return [h for h in db.q("select * from habits where active=1 order by id") if str(day.weekday()) in h["days"]]


def done_habits(day):
    return {r["habit_id"] for r in db.q("select habit_id from habit_log where day=?", iso(day))}


def streak(h, day):
    """予定の日だけ数えた連続日数。今日がまだなら昨日から数える"""
    logged = {r["day"] for r in db.q("select day from habit_log where habit_id=?", h["id"])}
    n, d, first = 0, day, True
    for _ in range(400):
        if str(d.weekday()) in h["days"]:
            if iso(d) in logged:
                n += 1
            elif not first:
                break
            first = False
        d -= dt.timedelta(days=1)
    return n


def board_tasks(day):
    return db.q("select * from tasks where deleted=0 and ((done_day is null and day<=?) or done_day=?) "
                "order by done_day is not null, day, id", iso(day), iso(day))


class TaskButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tb:t:(?P<id>\d+)"):
    def __init__(self, tid, label="タスク", done=False, row=None):
        super().__init__(discord.ui.Button(
            label=label, custom_id=f"tb:t:{tid}", row=row,
            style=discord.ButtonStyle.success if done else discord.ButtonStyle.secondary))
        self.tid = tid

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls(int(match["id"]))

    async def callback(self, it: discord.Interaction):
        t = db.one("select * from tasks where id=?", self.tid)
        if t:
            db.run("update tasks set done_day=? where id=?", None if t["done_day"] else iso(config.today()), self.tid)
        await it.client.get_cog("Today").redraw(it, config.today())


class HabitButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tb:h:(?P<id>\d+):(?P<day>[\d-]+)"):
    def __init__(self, hid, day, label="習慣", done=False, row=None):
        super().__init__(discord.ui.Button(
            label=label, custom_id=f"tb:h:{hid}:{day}", row=row,
            style=discord.ButtonStyle.primary if done else discord.ButtonStyle.secondary))
        self.hid, self.day = hid, day

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls(int(match["id"]), match["day"])

    async def callback(self, it: discord.Interaction):
        if db.one("select 1 from habit_log where habit_id=? and day=?", self.hid, self.day):
            db.run("delete from habit_log where habit_id=? and day=?", self.hid, self.day)
        else:
            db.run("insert into habit_log values(?, ?)", self.hid, self.day)
        await it.client.get_cog("Today").redraw(it, dt.date.fromisoformat(self.day))


class TidyMenu(discord.ui.DynamicItem[discord.ui.Select], template=r"tb:menu"):
    """まだのタスクを選ぶと、削除・明日へ・日付を変える を聞く"""

    def __init__(self, options=None):
        super().__init__(discord.ui.Select(custom_id="tb:menu", placeholder="🧹 タスクを整理（削除・明日へ）",
                                           options=options or [discord.SelectOption(label="-")], row=4))

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls()

    async def callback(self, it: discord.Interaction):
        tid = int(it.data["values"][0])
        t = db.one("select * from tasks where id=?", tid)
        cog = it.client.get_cog("Today")
        v = discord.ui.View(timeout=300)

        async def delete(i2):
            db.run("update tasks set deleted=1 where id=?", tid)
            await i2.response.edit_message(content=f"🗑️ 「{t['text']}」を消しました", view=None)
            await cog.refresh(config.today())

        async def tomorrow(i2):
            db.run("update tasks set day=? where id=?", iso(config.today() + dt.timedelta(days=1)), tid)
            await i2.response.edit_message(content=f"➡️ 「{t['text']}」を明日に回しました", view=None)
            await cog.refresh(config.today())

        async def other(i2):
            await i2.response.send_modal(DateModal(tid, t["text"], cog))
        for label, emoji, fn in (("消す", "🗑️", delete), ("明日へ", "➡️", tomorrow), ("日付を変える", "📅", other)):
            b = discord.ui.Button(label=label, emoji=emoji)
            b.callback = fn
            v.add_item(b)
        await it.response.send_message(f"「{t['text']}」をどうしますか？", view=v, ephemeral=True)


class DateModal(discord.ui.Modal, title="日付を変える"):
    when = discord.ui.TextInput(label="いつ（例：金曜、11/20、来週月曜）")

    def __init__(self, tid, text, cog):
        super().__init__()
        self.tid, self.text, self.cog = tid, text, cog

    async def on_submit(self, it: discord.Interaction):
        r = jptime.parse(self.when.value, config.now())
        if not r:
            return await it.response.send_message("日付を読み取れませんでした", ephemeral=True)
        db.run("update tasks set day=? where id=?", iso(r[0].date()), self.tid)
        await it.response.send_message(f"📅 「{self.text}」を {wd(r[0].date())} に回しました", ephemeral=True)
        await self.cog.refresh(config.today())


class Today(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(TaskButton, HabitButton, TidyMenu)
        if config.TODAY:
            self.rollover.start()
            self.nudge.start()

    async def cog_unload(self):
        self.rollover.cancel()
        self.nudge.cancel()

    # ---------- ボードの組み立て ----------
    def build(self, day):
        habits, done = habits_on(day), done_habits(day)
        rows = board_tasks(day)
        lines = []
        if habits:
            lines.append("**🔁 習慣**")
            for h in habits:
                s = streak(h, day)
                lines.append(f"{'✅' if h['id'] in done else '⬜'} {h['name']}" + (f"（🔥{s}日）" if s >= 2 else ""))
        if rows:
            lines.append("**📝 タスク**")
            for t in rows:
                carried = f"（{wd(dt.date.fromisoformat(t['day']))}から）" if t["day"] < iso(day) and not t["done_day"] else ""
                lines.append(f"✅ ~~{t['text']}~~" if t["done_day"] else f"⬜ {t['text']}{carried}")
        if not lines:
            lines.append(f"<#{config.TODAY}> に書くとタスクになります（1行1件、`金曜 〇〇` のように日付も書ける）")
        undone = sum(1 for t in rows if not t["done_day"])
        e = discord.Embed(title=f"📋 {wd(day)}", description="\n".join(lines)[:4096], color=0x3BA55D)
        e.set_footer(text=f"タスク 残り {undone} 件" + (f" ／ 習慣 {len(done & {h['id'] for h in habits})}/{len(habits)}" if habits else ""))

        v = discord.ui.View(timeout=None)
        items = [(HabitButton, h["id"], iso(day), f"🔁 {h['name']}"[:80], h["id"] in done) for h in habits]
        items += [(TaskButton, t["id"], ("✅ " if t["done_day"] else "") + t["text"][:40], bool(t["done_day"])) for t in rows]
        for i, (cls, *args) in enumerate(items[:MAX_BUTTONS]):
            v.add_item(cls(*args, row=i // 5))
        if len(items) > MAX_BUTTONS:
            e.set_footer(text=e.footer.text + f" ／ ボタンに入りきらない {len(items) - MAX_BUTTONS} 件があります")
        opts = [discord.SelectOption(label=t["text"][:100], value=str(t["id"])) for t in rows if not t["done_day"]][:25]
        if opts:
            v.add_item(TidyMenu(opts))
        return e, v

    async def post_board(self, day):
        ch = await channel(self.bot, config.TODAY)
        for b in db.q("select * from boards where day<=?", iso(day)):  # 前のボードはボタンを外す
            try:
                await ch.get_partial_message(b["message_id"]).edit(view=None)
            except discord.HTTPException:
                pass
            db.run("delete from boards where day=?", b["day"])
        e, v = self.build(day)
        m = await ch.send(embed=e, view=v)
        db.run("insert or replace into boards values(?, ?, ?)", iso(day), ch.id, m.id)

    async def refresh(self, day):
        b = db.one("select * from boards where day=?", iso(day))
        if not b:
            return await self.post_board(day)
        e, v = self.build(day)
        try:
            await (await channel(self.bot, b["channel_id"])).get_partial_message(b["message_id"]).edit(embed=e, view=v)
        except discord.NotFound:  # ボードが消されていたら出し直す
            db.run("delete from boards where day=?", iso(day))
            await self.post_board(day)

    async def redraw(self, it, day):
        """ボタンを押したときの描き直し（押したメッセージがそのボードならその場で書き換える）"""
        b = db.one("select * from boards where day=?", iso(day))
        if b and it.message and it.message.id == b["message_id"]:
            e, v = self.build(day)
            await it.response.edit_message(embed=e, view=v)
        else:
            await it.response.defer()
            await self.refresh(day)

    # ---------- 定時の処理 ----------
    @tasks.loop(time=dt.time(hour=config.DAY_START_HOUR, tzinfo=config.JST))
    async def rollover(self):
        await self.post_board(config.today())

    @tasks.loop(time=dt.time(hour=config.HABIT_NUDGE_HOUR, tzinfo=config.JST))
    async def nudge(self):
        day = config.today()
        left = [h["name"] for h in habits_on(day) if h["id"] not in done_habits(day)]
        if left:
            b = db.one("select * from boards where day=?", iso(day))
            link = f" → https://discord.com/channels/{config.GUILD}/{b['channel_id']}/{b['message_id']}" if b and config.GUILD else ""
            who = f"<@{config.OWNER}> " if config.OWNER else ""
            await (await channel(self.bot, config.TODAY)).send(f"{who}🌙 まだの習慣：{'、'.join(left)}{link}")

    @rollover.before_loop
    @nudge.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if config.TODAY and not db.one("select 1 from boards where day=?", iso(config.today())):
            await self.post_board(config.today())

    # ---------- タスクの追加 ----------
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.channel.id != config.TODAY or msg.author.bot:
            return
        today, later = config.today(), []
        for line in filter(None, (l.strip() for l in msg.content.splitlines())):
            r = jptime.parse(line, config.now())
            if r and r[2]:  # 日付が今日（夜中の2時の「今日」も含む）以前なら、今日のボードに出す
                day, text = (today if r[0].date() <= config.now().date() else r[0].date()), r[2]
            else:
                day, text = today, line
            db.run("insert into tasks(text, day) values(?, ?)", text, iso(day))
            if day > today:
                later.append(f"{wd(day)} {text}")
        try:
            await msg.delete()  # チャンネルをボードだけにしておく（メッセージの管理の権限が要る）
        except discord.HTTPException:
            pass
        if later:
            await msg.channel.send("📅 " + "、".join(later) + " に入れました", delete_after=8)
        await self.refresh(today)

    # ---------- 習慣のコマンド ----------
    habit = app_commands.Group(name="habit", description="習慣の登録と記録")

    @habit.command(name="add", description="習慣を足す（日：毎日・平日・週末・月水金 など）")
    async def habit_add(self, it: discord.Interaction, name: str, days: str = "毎日"):
        d = parse_days(days)
        if not d:
            return await it.response.send_message("曜日を読み取れませんでした（例：毎日、平日、月水金）", ephemeral=True)
        db.run("insert into habits(name, days) values(?, ?) on conflict(name) do update set days=excluded.days, active=1", name, d)
        await it.response.send_message(f"🔁 「{name}」（{days_label(d)}）を足しました", ephemeral=True)
        if config.TODAY:
            await self.refresh(config.today())

    @habit.command(name="remove", description="習慣をやめる（記録は残る）")
    async def habit_remove(self, it: discord.Interaction, name: str):
        db.run("update habits set active=0 where name=?", name)
        await it.response.send_message(f"「{name}」をやめました", ephemeral=True)
        if config.TODAY:
            await self.refresh(config.today())

    @habit_remove.autocomplete("name")
    async def _names(self, it, current: str):
        return [app_commands.Choice(name=h["name"], value=h["name"])
                for h in db.q("select name from habits where active=1 and name like ?", f"%{current}%")][:25]

    @habit.command(name="stats", description="習慣の連続日数と4週間の記録")
    async def habit_stats(self, it: discord.Interaction):
        day = config.today()
        hs = db.q("select * from habits where active=1 order by id")
        if not hs:
            return await it.response.send_message("習慣がまだありません（/habit add で足せます）", ephemeral=True)
        start = day - dt.timedelta(days=day.weekday() + 21)  # 4週間前の月曜
        e = discord.Embed(title="🔁 習慣の記録（4週間、月曜はじまり）", color=0x5865F2)
        for h in hs:
            logged = {r["day"] for r in db.q("select day from habit_log where habit_id=?", h["id"])}
            grid = []
            for w in range(4):
                row = ""
                for i in range(7):
                    d = start + dt.timedelta(days=w * 7 + i)
                    row += ("⬜" if d > day else "🟩" if iso(d) in logged else
                            "⬛" if str(d.weekday()) in h["days"] else "▫️")
                grid.append(row)
            sched = [day - dt.timedelta(days=i) for i in range(30) if str((day - dt.timedelta(days=i)).weekday()) in h["days"]]
            rate = sum(iso(d) in logged for d in sched) / len(sched) if sched else 0
            e.add_field(name=f"{h['name']}（{days_label(h['days'])}）🔥{streak(h, day)}日 ／ 30日で{rate:.0%}",
                        value="\n".join(grid), inline=False)
        e.set_footer(text="🟩 できた ⬛ できなかった ▫️ 予定なし ⬜ これから")
        await it.response.send_message(embed=e)

    @app_commands.command(name="board", description="今日のボードを出し直す")
    async def board(self, it: discord.Interaction):
        await it.response.send_message("出し直しました", ephemeral=True)
        await self.post_board(config.today())


async def setup(bot):
    await bot.add_cog(Today(bot))
