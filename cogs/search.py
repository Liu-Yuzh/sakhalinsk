# cogs/search.py : ばらばらに置いたメモを、まとめて探す（SQLite の全文検索、LLM なし）
#   ・Discord のメッセージ（貼ったリンクはページのタイトルも取って一緒に入れる）
#   ・Cosense の全ページ（数時間おきに、更新されたページだけ読み直す）
#   ・レシートの OCR の文字、論文 PDF の序論・結論
#   探し方：#search に書く か /find。「@cosense 〇〇」のように場所で絞れる
#   /triage：Discord に貼ったまま Cosense に入っていないリンクを並べて、ボタンで Cosense へ送る
import asyncio
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import search_index
import sources

SKIP_URL = ("discord.com", "discord.gg", "scrapbox.io", "cdn.discordapp.com", "media.discordapp.net")


class Search(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sem = asyncio.Semaphore(5)

    async def cog_load(self):
        self.sync.change_interval(hours=config.COSENSE_SYNC_HOURS)
        self.sync.start()

    async def cog_unload(self):
        self.sync.cancel()

    # ---------- Discord ----------
    async def url_title(self, url):
        if r := db.one("select title from url_titles where url=?", url):
            return r["title"]
        async with self.sem:
            p = await sources.web_page(self.bot.web, url)
        title = p["title"] if p["title"] != url else ""
        db.run("insert or replace into url_titles values(?, ?)", url, title)
        return title

    async def index_message(self, m: discord.Message):
        if m.author.bot or not m.guild or m.channel.id == config.SEARCH:
            return
        urls = [u.rstrip(">)") for u in sources.URL_RE.findall(m.content) if not any(s in u for s in SKIP_URL)]
        titles = [t for t in await asyncio.gather(*(self.url_title(u) for u in urls)) if t]
        body = "\n".join([m.content, *(f"🔗 {t}" for t in titles), *(a.filename for a in m.attachments)]).strip()
        if not body:
            return
        ch = m.channel
        where = f"#{ch.parent.name} › {ch.name}" if isinstance(ch, discord.Thread) and ch.parent else f"#{getattr(ch, 'name', '?')}"
        search_index.put("discord", m.id, where, body, m.jump_url, m.created_at.astimezone(config.JST).isoformat())

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if m.channel.id == config.SEARCH and not m.author.bot:
            return await m.reply(**self.results(m.content))
        await self.index_message(m)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, ev):
        if ev.message and ev.message.guild:
            await self.index_message(ev.message)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, ev):
        search_index.drop("discord", ev.message_id)

    async def backfill_discord(self, progress):
        guild = self.bot.get_guild(config.GUILD) or self.bot.guilds[0]
        chans = [*guild.text_channels, *guild.threads]
        for forum in guild.forums:
            chans += forum.threads
            chans += [t async for t in forum.archived_threads(limit=None)]
        n = 0
        for ch in chans:
            try:
                async for m in ch.history(limit=None):
                    await self.index_message(m)
                    n += 1
            except discord.Forbidden:
                continue
            await progress(f"💬 {getattr(ch, 'name', ch.id)} まで、{n} 件")
        return n

    # ---------- Cosense ----------
    async def sync_cosense(self):
        """更新されたページだけ読み直す。(読んだ数, 消した数)"""
        pages = dict(await self.bot.cs.list_pages())
        known = {r["title"]: r["updated"] for r in db.q("select * from cosense_meta")}
        n = 0
        for title, updated in pages.items():
            if known.get(title) == updated:
                continue
            text = await self.bot.cs.text(title)
            if text is not None:
                at = dt.datetime.fromtimestamp(updated, config.JST).isoformat()
                search_index.put("cosense", title, title, text, self.bot.cs.page_url(title), at)
                db.run("insert or replace into cosense_meta values(?, ?)", title, updated)
                n += 1
            await asyncio.sleep(0.3)  # Cosense に負担をかけない
        gone = set(known) - set(pages)
        for title in gone:
            search_index.drop("cosense", title)
            db.run("delete from cosense_meta where title=?", title)
        return n, len(gone)

    @tasks.loop(hours=6)
    async def sync(self):
        try:
            n, gone = await self.sync_cosense()
            print(f"Cosense の索引：{n} 件を更新、{gone} 件を削除")
        except PermissionError as ex:
            print(f"Cosense の索引を作れません：{ex}")
        except Exception as ex:  # ネットの不調などは次の回に任せる
            print(f"Cosense の索引に失敗：{type(ex).__name__}: {ex}")

    @sync.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ---------- 探す ----------
    def results(self, text):
        hits = search_index.search(text, limit=10)
        if not hits:
            return {"content": "見つかりませんでした（`@cosense` `@discord` `@receipt` `@pdf` で場所を絞れます）"}
        e = discord.Embed(title=f"🔎 {text[:200]}", color=0x5865F2)
        lines = []
        for h in hits:
            icon = search_index.SOURCES.get(h["source"], "・")
            lines.append(f"{icon} **[{h['title'][:60]}]({h['url']})** {h['at'][:10]}\n　{h['snippet'][:150]}")
        e.description = "\n".join(lines)[:4096]
        return {"embed": e}

    @app_commands.command(name="find", description="Discord・Cosense・レシート・PDF をまとめて探す（@cosense などで絞れる）")
    async def find(self, it: discord.Interaction, query: str):
        await it.response.send_message(**self.results(query))

    @app_commands.command(name="reindex", description="検索の索引を作り直す（初回に実行）")
    @app_commands.choices(target=[app_commands.Choice(name="Cosense", value="cosense"),
                                  app_commands.Choice(name="Discord の過去のメッセージ", value="discord")])
    async def reindex(self, it: discord.Interaction, target: app_commands.Choice[str]):
        await it.response.send_message(f"{target.name} の索引を作っています…")
        msg = await it.original_response()

        async def progress(t):
            await msg.edit(content=f"{target.name} の索引を作っています… {t}")
        if target.value == "cosense":
            db.run("delete from cosense_meta")
            n, gone = await self.sync_cosense()
            await msg.edit(content=f"✅ Cosense：{n} ページを索引に入れました")
        else:
            n = await self.backfill_discord(progress)
            await msg.edit(content=f"✅ Discord：{n} 件のメッセージを索引に入れました")

    # ---------- 未整理のリンク ----------
    @app_commands.command(name="triage", description="Discord に貼ったまま Cosense にないリンクを並べる")
    @app_commands.describe(days="何日前まで見るか")
    async def triage(self, it: discord.Interaction, days: int = 14):
        since = (config.now() - dt.timedelta(days=days)).isoformat()
        seen, items = set(), []
        for r in db.q("select * from docs where source='discord' and at>=? and body like '%http%' order by at desc", since):
            for u in sources.URL_RE.findall(r["body"]):
                u = u.rstrip(">)")
                if u in seen or any(s in u for s in SKIP_URL):
                    continue
                seen.add(u)
                if not db.one("select 1 from docs where source='cosense' and body like ?", f"%{u}%"):
                    items.append((u, r["url"]))
        if not items:
            return await it.response.send_message(f"この {days} 日で、Cosense にないリンクはありません", ephemeral=True)
        items = items[:10]
        lines = []
        for i, (u, jump) in enumerate(items, 1):
            t = (db.one("select title from url_titles where url=?", u) or {"title": ""})["title"] or u
            lines.append(f"**{i}.** [{t[:70]}]({u}) （[貼った場所]({jump})）")
        v = discord.ui.View(timeout=900)
        for i, (u, _) in enumerate(items, 1):
            b = discord.ui.Button(label=f"{i} を Cosense へ", emoji="📥", row=(i - 1) // 5)

            async def go(i2, u=u, b=b):
                await i2.response.defer(thinking=True)
                b.disabled = True
                await i2.followup.send(**await self.bot.get_cog("Papers").clip(u))
            b.callback = go
            v.add_item(b)
        e = discord.Embed(title=f"📥 Cosense にないリンク（{days} 日）", description="\n".join(lines), color=0xF0B232)
        await it.response.send_message(embed=e, view=v)


async def setup(bot):
    await bot.add_cog(Search(bot))
