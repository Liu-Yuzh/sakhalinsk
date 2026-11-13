# cogs/remind.py : 「明日9時 ゴミ出し」と書くだけのリマインダー。時間が来たら 完了・10分後・1時間後・明日 のボタン
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import jptime

SNOOZE = {"s10": dt.timedelta(minutes=10), "s60": dt.timedelta(hours=1)}
HINT = "日時を読み取れませんでした。例：`明日9時 ゴミ出し` `30分後 洗濯` `毎週月曜8時 資源ごみ` `11/20 15:00 締切`"


def repeat_label(r):
    if not r:
        return ""
    if r == "daily":
        return "（毎日）"
    if r == "weekdays":
        return "（平日）"
    return "（毎週 " + "・".join(jptime.WD[int(i)] for i in r[7:].split(",")) + "）"


def add(user_id, channel_id, text, due, repeat):
    return db.run("insert into reminders(user_id, channel_id, text, due, repeat) values(?,?,?,?,?)",
                  user_id, channel_id, text, due.replace(microsecond=0).isoformat(), repeat)


class RemindButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rm:(?P<id>\d+):(?P<a>\w+)"):
    LABELS = {"done": ("完了", "✅"), "s10": ("10分後", "⏱️"), "s60": ("1時間後", "⏱️"),
              "tmr": ("明日", "🌅"), "cancel": ("取り消す", "🗑️")}

    def __init__(self, rid, action):
        label, emoji = self.LABELS[action]
        style = discord.ButtonStyle.success if action == "done" else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"rm:{rid}:{action}"))
        self.rid, self.action = rid, action

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls(int(match["id"]), match["a"])

    async def callback(self, it: discord.Interaction):
        r = db.one("select * from reminders where id=?", self.rid)
        if not r:
            return await it.response.send_message("見つかりません", ephemeral=True)
        if self.action == "cancel":
            db.run("update reminders set done=2 where id=?", self.rid)
            return await it.response.edit_message(content=f"🗑️ ~~{r['text']}~~ を取り消しました", view=None)
        if self.action == "done":
            return await it.response.edit_message(content=f"✅ ~~{r['text']}~~", view=None)
        now = config.now()
        due = now + SNOOZE[self.action] if self.action in SNOOZE else jptime.parse("明日", now)[0]
        add(r["user_id"], r["channel_id"], r["text"], due, None)
        await it.response.edit_message(content=f"⏰ {r['text']} → {jptime.fmt(due)} にもう一度", view=None)


def view_of(rid, *actions):
    v = discord.ui.View(timeout=None)
    for a in actions:
        v.add_item(RemindButton(rid, a))
    return v


class Remind(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(RemindButton)
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    def register(self, user_id, channel_id, text):
        """(返事の文, 取り消しボタン) / 読めなければ (ヒント, None)"""
        r = jptime.parse(text, config.now())
        if not r:
            return HINT, None
        due, repeat, body = r
        rid = add(user_id, channel_id, body or "リマインド", due, repeat)
        return f"⏰ {jptime.fmt(due)}「{body or 'リマインド'}」{repeat_label(repeat)}", view_of(rid, "cancel")

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.channel.id != config.REMIND or msg.author.bot:
            return
        text, view = self.register(msg.author.id, msg.channel.id, msg.content)
        await msg.reply(text, **({"view": view} if view else {}))

    @app_commands.command(name="remind", description="リマインダーを入れる（例：明日9時 ゴミ出し）")
    async def remind(self, it: discord.Interaction, text: str):
        content, view = self.register(it.user.id, it.channel_id, text)
        await it.response.send_message(content, **({"view": view} if view else {"ephemeral": True}))

    @app_commands.command(name="reminders", description="これからのリマインダーの一覧（取り消しもできる）")
    async def reminders(self, it: discord.Interaction):
        rows = db.q("select * from reminders where done=0 order by due limit 25")
        if not rows:
            return await it.response.send_message("予定されたリマインダーはありません", ephemeral=True)
        lines = [f"・{jptime.fmt(dt.datetime.fromisoformat(r['due']))} {r['text']}{repeat_label(r['repeat'])}" for r in rows]
        v = discord.ui.View(timeout=300)
        sel = discord.ui.Select(placeholder="取り消すものを選ぶ", options=[
            discord.SelectOption(label=f"{r['text'][:60]}", description=jptime.fmt(dt.datetime.fromisoformat(r["due"])),
                                 value=str(r["id"])) for r in rows])

        async def cancel(i2):
            rid = int(i2.data["values"][0])
            db.run("update reminders set done=2 where id=?", rid)
            await i2.response.send_message("取り消しました", ephemeral=True)
        sel.callback = cancel
        v.add_item(sel)
        await it.response.send_message("\n".join(lines), view=v, ephemeral=True)

    @tasks.loop(seconds=30)
    async def tick(self):
        now = config.now().replace(microsecond=0)  # 文字列で比べるので、保存の形とそろえる
        for r in db.q("select * from reminders where done=0 and due<=? order by due", now.isoformat()):
            due = dt.datetime.fromisoformat(r["due"])
            ch = self.bot.get_channel(r["channel_id"])
            if ch:
                late = "（Bot が止まっていたので遅れました）" if now - due > dt.timedelta(minutes=5) else ""
                await ch.send(f"<@{r['user_id']}> ⏰ **{r['text']}**{late}",
                              view=view_of(r["id"], "done", "s10", "s60", "tmr"))
            if r["repeat"]:
                nxt = jptime.next_due(r["repeat"], max(due, now), (due.hour, due.minute))
                db.run("update reminders set due=? where id=?", nxt.replace(microsecond=0).isoformat(), r["id"])
            else:
                db.run("update reminders set done=1 where id=?", r["id"])

    @tick.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Remind(bot))
