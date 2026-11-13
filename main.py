# main.py : Sakhalinsk ― 研究メモと身の回りの管理をする Discord Bot（LLM は使わない）
#   機能ごとのコードは cogs/ に分けてある
import aiohttp
import discord
from discord.ext import commands

import config
import cosense

EXTENSIONS = ["cogs.papers", "cogs.arxiv", "cogs.search", "cogs.remind", "cogs.today", "cogs.kakeibo", "cogs.kindle"]


class Sakhalinsk(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # 貼られた URL やタスクの文を読むため
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        self.web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        # Cosense は Cookie でログインするので、返ってきた Cookie を覚えない専用のセッションにする
        #（覚えると、未ログインの Cookie が .env の Cookie と一緒に送られて、ログインが効かなくなる）
        self.cosense_web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                                 cookie_jar=aiohttp.DummyCookieJar())
        self.cs = cosense.Cosense(self.cosense_web, config.PROJECT, config.SID, config.WRITE)
        config.DATA.mkdir(parents=True, exist_ok=True)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        guild = discord.Object(config.GUILD) if config.GUILD else None
        if guild:  # サーバーを指定すると、コマンドがすぐ反映される
            self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def close(self):
        await self.web.close()
        await self.cosense_web.close()
        await super().close()


bot = Sakhalinsk()


@bot.tree.error
async def on_app_error(it: discord.Interaction, err):
    msg = f"失敗しました：{getattr(err, 'original', err)}"
    if it.response.is_done():
        await it.followup.send(msg, ephemeral=True)
    else:
        await it.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    bot.run(config.TOKEN)
