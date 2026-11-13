# cogs/papers.py : 論文の登録（Cosense）、論文ごとのフォーラムスレッド、積読の状態、PDF の抽出
import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

import config
import cosense
import db
import pdftools
import search_index
import sources
from util import chunks, md_links, reply

LINK_LIMIT = 3800  # 埋め込みの説明欄は 4096 文字まで
EMOJI = {"積読": "📚", "読書中": "📖", "読了": "✅"}


class StatusButton(discord.ui.DynamicItem[discord.ui.Button], template=r"st:(?P<tid>\d+):(?P<i>\d)"):
    """スレッドの最初の投稿に付く「積読・読書中・読了」のボタン（再起動しても効く）"""

    def __init__(self, tid, i, current=None):
        s = cosense.STATUSES[i]
        style = discord.ButtonStyle.primary if s == current else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label=s, emoji=EMOJI[s], style=style, custom_id=f"st:{tid}:{i}"))
        self.tid, self.i = tid, i

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls(int(match["tid"]), int(match["i"]))

    async def callback(self, it: discord.Interaction):
        row = db.one("select page from papers where thread_id=?", self.tid)
        if not row:
            return await it.response.send_message("この論文の記録が見つかりません", ephemeral=True)
        await it.response.defer(ephemeral=True, thinking=True)
        note = await it.client.get_cog("Papers").set_status(row["page"], cosense.STATUSES[self.i])
        await it.followup.send(f"「{cosense.STATUSES[self.i]}」にしました{note}", ephemeral=True)


def status_view(tid, current):
    v = discord.ui.View(timeout=None)
    for i in range(len(cosense.STATUSES)):
        v.add_item(StatusButton(tid, i, current))
    return v


class Papers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(StatusButton)

    @property
    def cs(self):
        return self.bot.cs

    # ---------- 登録 ----------
    async def clip(self, text, force=False):
        p = await sources.resolve(self.bot.web, text)
        if not p:
            return reply("URL・arXiv・DOI が見つかりませんでした")
        title = cosense.page_title(p)
        lines = cosense.web_lines(p) if p["kind"] == "web" else cosense.paper_lines(p)

        note = ""
        if not force:
            known = p.get("id") and db.one("select thread_id from papers where pid=?", p["id"])
            try:
                # shortcut: Web ページは URL の文字列そのままで検索する。ヒットしにくければ yuzh-dupcheck の urlKey を移植する
                hits = [title] if await self.cs.exists(title) else []
                hits += [t for t in await self.cs.search(p.get("id") or p["url"]) if t not in hits]
            except PermissionError:  # 非公開で COSENSE_SID なし：確認を飛ばしてリンクだけ返す
                hits, note = [], "\n（非公開のため重複は確認していません）"
            if hits or known:
                msg = "もう登録してありそうです（作るなら `/clip force:True`）"
                if hits:
                    msg += "\n" + md_links(self.cs, hits[:5])
                if known and known["thread_id"]:
                    msg += f"\n💬 スレッド：<#{known['thread_id']}>"
                return reply(msg)

        e = discord.Embed(title=title[:256], url=p["url"], color=0x2F4FA8)
        if p["kind"] != "web":
            cited = f" ／ 被引用 {p['citations']}" if p.get("citations") is not None else ""
            e.set_footer(text=f"{', '.join(p['authors'][:3])} ／ {p['year']} {p.get('venue', '')}{cited}"[:2048])
            if p.get("tldr"):
                e.add_field(name="TLDR", value=p["tldr"][:1024], inline=False)

        file = None
        if self.cs.can_write:
            try:
                await self.cs.create(title, lines)
            except FileExistsError:
                return reply("同じタイトルのページがあるので、上書きしないよう書き込みをやめました")
            e.description = f"✅ Cosense に作りました → [{title}]({self.cs.page_url(title)})"
        else:
            url = self.cs.page_url(title, lines)
            if len(url) <= LINK_LIMIT:
                e.description = f"[📝 Cosense で作成]({url})"
            else:
                file = discord.File(io.BytesIO("\n".join(lines).encode()), "body.txt")
                e.description = f"本文が長いので、[空のページ]({self.cs.page_url(title)})に body.txt を貼ってください"
        if p["kind"] == "web":
            e.description += f"\n[✏️ メモ送信エディタで開く]({cosense.editor_url(config.EDITOR, p)})"
        else:
            db.run("insert or ignore into papers(page, pid) values(?, ?)", title, p["id"])
            thread = await self.open_thread(p, title, e)
            if thread:
                e.description += f"\n💬 スレッド：{thread.mention}"
        e.description += note
        return reply(embed=e, file=file)

    # ---------- フォーラムのスレッド ----------
    async def forum(self):
        ch = self.bot.get_channel(config.PAPER_FORUM)
        return ch if isinstance(ch, discord.ForumChannel) else None

    async def ensure_tags(self, forum):
        """フォーラムに「積読・読書中・読了」のタグがなければ作る（チャンネルの管理の権限が要る）"""
        have = {t.name for t in forum.available_tags}
        missing = [discord.ForumTag(name=s, emoji=EMOJI[s]) for s in cosense.STATUSES if s not in have]
        if missing:
            try:
                await forum.edit(available_tags=[*forum.available_tags, *missing])
            except discord.Forbidden:
                print("フォーラムのタグを作れませんでした。手で「積読」「読書中」「読了」を作ってください")

    async def open_thread(self, p, title, embed):
        forum = await self.forum()
        if not forum:
            return None
        await self.ensure_tags(forum)
        tags = [t for t in forum.available_tags if t.name == "積読"]
        e = embed.copy()
        if p.get("abstract"):
            e.add_field(name="概要", value=p["abstract"][:1024], inline=False)
        tm = await forum.create_thread(name=title[:100], embed=e, applied_tags=tags)
        await tm.message.edit(view=status_view(tm.thread.id, "積読"))
        db.run("update papers set thread_id=?, status='積読' where page=?", tm.thread.id, title)
        if config.PDF_AUTO and p["kind"] == "arxiv":
            asyncio.create_task(self.auto_pdf(tm.thread, p))
        return tm.thread

    async def auto_pdf(self, thread, p):
        try:
            async with self.bot.web.get(f"https://arxiv.org/pdf/{p['id']}", headers=sources.UA) as r:
                r.raise_for_status()
                data = await r.read()
        except Exception as ex:
            return await thread.send(f"PDF を取れませんでした：{ex}")
        await self.post_extract(thread, data, p["id"], p["title"])

    async def post_extract(self, ch, data, name, title):
        """PDF を保存して、序論の最後・結論・図を投稿し、検索にも入れる"""
        folder = config.DATA / "papers"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.pdf").write_bytes(data)
        try:
            intro, concl, figs = await asyncio.to_thread(pdftools.extract, data)
        except Exception as ex:
            return await ch.send(f"PDF を読めませんでした：{ex}")
        for head, text in (("📄 序論の最後", intro), ("📄 結論", concl)):
            for i, part in enumerate(chunks(text)):
                await ch.send((f"**{head}**\n" if i == 0 else "") + part)
        for i in range(0, len(figs), 10):
            await ch.send(files=[discord.File(io.BytesIO(png), f"fig{n}.png") for n, png in figs[i:i + 10]])
        if not (intro or concl or figs):
            await ch.send("見出しや図を見つけられませんでした（PDF の書式によっては取れません）")
        url = ch.jump_url if hasattr(ch, "jump_url") else ""
        search_index.put("pdf", name, title, f"{intro}\n{concl}", url, config.now().isoformat())

    # ---------- 積読の状態 ----------
    async def set_status(self, page, status):
        """Cosense のタグ・フォーラムのタグ・ボタンの見た目をそろえて変える。注意書きを返す"""
        note = ""
        if self.cs.can_write:
            try:
                await self.cs.update(page, lambda lines: cosense.set_status(lines, status))
            except FileNotFoundError:
                note = "（Cosense にページがないので、Discord 側だけ変えました）"
        else:
            note = f"（Cosense の[ページ]({self.cs.page_url(page)})のタグは手で #{status} に変えてください）"
        db.run("update papers set status=? where page=?", status, page)
        row = db.one("select thread_id from papers where page=?", page)
        if row and row["thread_id"]:
            try:
                thread = self.bot.get_channel(row["thread_id"]) or await self.bot.fetch_channel(row["thread_id"])
                tags = {t.name: t for t in thread.parent.available_tags}
                keep = [t for t in thread.applied_tags if t.name not in cosense.STATUSES]
                await thread.edit(applied_tags=(keep + [tags[status]] if status in tags else keep)[:5])
                starter = await thread.fetch_message(thread.id)  # フォーラムの最初の投稿の ID はスレッドと同じ
                await starter.edit(view=status_view(thread.id, status))
            except discord.HTTPException as ex:
                note += f"（スレッドの更新に失敗：{ex}）"
        return note

    async def shelf(self):
        """{状態: [ページ名, ...]}。Cosense のタグを正とし、読めなければ bot.db の記録を使う"""
        try:
            out = {s: await self.cs.tagged(cosense.PAPER_TAG, s) for s in cosense.STATUSES}
            for s, pages in out.items():
                for pg in pages:
                    db.run("update papers set status=? where page=?", s, pg)
            return out, ""
        except PermissionError:
            out = {s: [r["page"] for r in db.q("select page from papers where status=? order by rowid", s)]
                   for s in cosense.STATUSES}
            return out, "（Cosense を読めないので、Bot の記録から出しています）"

    @app_commands.command(name="tsundoku", description="論文の積読・読書中・読了を一覧して、状態を変える")
    async def tsundoku(self, it: discord.Interaction):
        await it.response.defer(thinking=True)
        shelf, note = await self.shelf()
        threads = {r["page"]: r["thread_id"] for r in db.q("select page, thread_id from papers where thread_id is not null")}

        def line(pg):
            link = f" · <#{threads[pg]}>" if pg in threads else ""
            return f"・[{pg[:60]}]({self.cs.page_url(pg)}){link}"

        e = discord.Embed(title="📚 論文の本棚", color=0x2F4FA8, description=note or None)
        for s in ("読書中", "積読"):
            pages = shelf[s]
            body = "\n".join(line(pg) for pg in pages[:12]) or "なし"
            if len(pages) > 12:
                body += f"\n…ほか {len(pages) - 12} 件"
            e.add_field(name=f"{EMOJI[s]} {s}（{len(pages)}）", value=body[:1024], inline=False)
        e.set_footer(text=f"読了 {len(shelf['読了'])} 件")
        view = ShelfView(self, shelf["読書中"] + shelf["積読"])
        await it.followup.send(embed=e, view=view if view.children else discord.utils.MISSING)

    @app_commands.command(name="clip", description="URL・arXiv・DOI から Cosense のページを作る")
    @app_commands.describe(url="URL、arXiv ID（arxiv:2401.12345）、DOI", force="重複していても作る")
    async def clip_cmd(self, it: discord.Interaction, url: str, force: bool = False):
        await it.response.defer(thinking=True)
        await it.followup.send(**await self.clip(url, force))

    # ---------- メッセージ ----------
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return
        pdfs = [a for a in msg.attachments if a.filename.lower().endswith(".pdf")]
        in_paper_thread = isinstance(msg.channel, discord.Thread) and msg.channel.parent_id == config.PAPER_FORUM
        if pdfs and (in_paper_thread or msg.channel.id == config.INBOX):
            async with msg.channel.typing():
                for a in pdfs:
                    name = str(msg.channel.id if in_paper_thread else msg.id)
                    title = msg.channel.name if in_paper_thread else a.filename
                    await self.post_extract(msg.channel, await a.read(), name, title)
            return
        if msg.channel.id != config.INBOX:
            return
        if not any(r.search(msg.content) for r in (sources.URL_RE, sources.ARXIV_RE, sources.DOI_RE)):
            return
        async with msg.channel.typing():
            try:
                await msg.reply(**await self.clip(msg.content))
            except Exception as ex:  # 個人用なので、原因をそのまま見せる
                await msg.reply(f"失敗しました：{type(ex).__name__}: {ex}")


class ShelfView(discord.ui.View):
    """/tsundoku の下に付く「状態を変える」メニュー（その場限り）"""

    def __init__(self, cog, pages):
        super().__init__(timeout=600)
        self.cog = cog
        if pages:
            sel = discord.ui.Select(placeholder="状態を変える論文を選ぶ",
                                    options=[discord.SelectOption(label=pg[:100], value=str(i))
                                             for i, pg in enumerate(pages[:25])])
            sel.callback = self.pick
            self.pages = pages[:25]
            self.add_item(sel)

    async def pick(self, it: discord.Interaction):
        page = self.pages[int(it.data["values"][0])]
        v = discord.ui.View(timeout=300)
        for s in cosense.STATUSES:
            b = discord.ui.Button(label=s, emoji=EMOJI[s])

            async def go(i2, s=s):
                await i2.response.defer(ephemeral=True, thinking=True)
                note = await self.cog.set_status(page, s)
                await i2.followup.send(f"「{page[:60]}」を「{s}」にしました{note}", ephemeral=True)
            b.callback = go
            v.add_item(b)
        await it.response.send_message(f"「{page[:80]}」をどうしますか？", view=v, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Papers(bot))
