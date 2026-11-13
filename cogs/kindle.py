# cogs/kindle.py : 毎朝 Kindle の日替わりセールを流す
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import sources
from util import channel


class Kindle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if config.KINDLE:
            self.daily.start()

    async def cog_unload(self):
        self.daily.cancel()

    async def post(self):
        ch = await channel(self.bot, config.KINDLE)
        deals = await sources.kindle_deals(self.bot.web)
        if not deals:
            await ch.send("Kindle の日替わりセールを読めませんでした（ページの作りが変わったかもしれません）")
            return 0
        lines = [f"・[{t[:80]}]({u}) {p}" for t, u, p in deals]
        text = "\n".join(lines)
        while len(text) > 4096:  # 埋め込みの上限
            lines.pop()
            text = "\n".join(lines)
        e = discord.Embed(title=f"Kindle 日替わりセール {config.now():%m/%d}", url=sources.KINDLE_DEALS, description=text)
        await ch.send(embed=e)
        return len(deals)

    @tasks.loop(time=datetime.time(hour=config.KINDLE_HOUR, tzinfo=config.JST))
    async def daily(self):
        await self.post()

    @daily.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="kindle_now", description="Kindle の日替わりセールを今すぐ流す")
    async def now(self, it: discord.Interaction):
        await it.response.defer(thinking=True)
        await it.followup.send(f"{await self.post()} 冊を流しました")


async def setup(bot):
    await bot.add_cog(Kindle(bot))
