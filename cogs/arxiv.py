# cogs/arxiv.py : 毎朝 arXiv の新着をキーワードで絞って通知。📌 で登録、👎 で見送り（反応は記録）
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import sources
from util import channel

PIN, NG = "📌", "👎"


def score(p):
    text = f"{p['title']} {p['abstract']}".lower()
    return [k for k in config.KEYWORDS if k in text]


class Arxiv(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if config.WATCH:
            self.daily.start()

    async def cog_unload(self):
        self.daily.cancel()

    async def post(self):
        ch = await channel(self.bot, config.WATCH)
        papers = await sources.arxiv_latest(self.bot.web, config.ARXIV_CATS)
        new = [p for p in papers if not db.one("select 1 from seen where id=?", p["id"])]
        db.con.executemany("insert or ignore into seen values(?)", [(p["id"],) for p in new])
        db.con.commit()
        picked = sorted(((score(p), p) for p in new), key=lambda x: -len(x[0]))
        picked = [(h, p) for h, p in picked if h or not config.KEYWORDS][:config.WATCH_MAX]
        if not picked:
            await ch.send(f"新着 {len(new)} 件に、キーワードの合う論文はありませんでした")
        for hits, p in picked:
            e = discord.Embed(title=p["title"][:256], url=p["url"], description=p["abstract"][:400] + "…")
            foot = [", ".join(p["authors"][:3]), "一致：" + ", ".join(hits) if hits else ""]
            e.set_footer(text=" ／ ".join(filter(None, foot)))
            m = await ch.send(embed=e)
            await m.add_reaction(PIN)
            await m.add_reaction(NG)
        return len(picked)

    @tasks.loop(time=datetime.time(hour=config.WATCH_HOUR, tzinfo=config.JST))
    async def daily(self):
        await self.post()

    @daily.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="arxiv_now", description="arXiv の新着チェックを今すぐ行う")
    async def now(self, it: discord.Interaction):
        await it.response.defer(thinking=True)
        await it.followup.send(f"{await self.post()} 件を通知しました")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, ev):
        # 反応はメッセージに残るので、再起動のあとでも効く
        if ev.channel_id != config.WATCH or ev.user_id == self.bot.user.id or str(ev.emoji) not in (PIN, NG):
            return
        msg = await (await channel(self.bot, config.WATCH)).fetch_message(ev.message_id)
        m = msg.embeds and sources.ARXIV_RE.search(msg.embeds[0].url or "")
        if not m:
            return
        e = msg.embeds[0]
        db.run("insert or replace into feedback(id, label, title, abstract) values(?,?,?,?)",
               m.group(1), int(str(ev.emoji) == PIN), e.title, e.description)
        if str(ev.emoji) == PIN:
            await msg.reply(**await self.bot.get_cog("Papers").clip(e.url))


async def setup(bot):
    await bot.add_cog(Arxiv(bot))
