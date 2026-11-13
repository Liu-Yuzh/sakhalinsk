# cogs/kakeibo.py : 家計簿。#kakeibo にレシートの写真を送るか「680 昼食」と書く
#   ・写真はサーバーの data/receipts/年-月/ に保存し、Tesseract で読む
#   ・分類はボタンで選ぶ。同じ店（またはメモ）で選んだ分類は覚えて、次から自動で付ける
import asyncio
import csv
import datetime as dt
import io
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import receipt
import search_index

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def key_of(s):
    """店名やメモを、分類を覚えるための鍵にする（空白・記号・数字を落とす。「〇〇店」の支店名も落とす）"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"\S+店$", "", s.strip()) or s
    return re.sub(r"[\s\d\W_]+", "", s)


def guess(store, memo):
    for k in (key_of(store), key_of(memo)):
        if k and (r := db.one("select category from store_cat where store=?", k)):
            return r["category"]
    return None


def embed(x):
    e = discord.Embed(title=f"🧾 ¥{x['amount']:,}" if x["amount"] else "🧾 金額を読めませんでした（✏️ で直してください）",
                      color=0xF0B232 if not x["category"] else 0x3BA55D)
    e.add_field(name="日付", value=x["day"])
    e.add_field(name="店・メモ", value=" ／ ".join(filter(None, [x["store"], x["memo"]])) or "-")
    e.add_field(name="分類", value=x["category"] or "未分類（下のボタンで選ぶ）")
    e.set_footer(text=f"#{x['id']}" + ("　写真はサーバーに保存済み" if x["image"] else ""))
    return e


def view(x):
    v = discord.ui.View(timeout=None)
    for i, c in enumerate(config.CATEGORIES[:15]):
        v.add_item(ExpenseButton(x["id"], f"c{i}", c, c == x["category"], row=i // 5))
    row = min(len(config.CATEGORIES[:15]) // 5 + (1 if len(config.CATEGORIES) % 5 else 0), 4)
    v.add_item(ExpenseButton(x["id"], "edit", "直す", row=row))
    v.add_item(ExpenseButton(x["id"], "del", "取り消す", row=row))
    return v


def get(eid):
    return db.one("select * from expenses where id=?", eid)


def index(x, url):
    search_index.put("receipt", x["id"], f"{x['store'] or x['memo'] or '支出'} ¥{x['amount'] or '?'}",
                     " ".join(filter(None, [x["memo"], x["category"], x["ocr"]])), url, x["day"])


class ExpenseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"kb:(?P<id>\d+):(?P<a>c\d+|edit|del)"):
    def __init__(self, eid, action, label="-", on=False, row=None):
        style = (discord.ButtonStyle.success if on else discord.ButtonStyle.secondary) if action.startswith("c") \
            else discord.ButtonStyle.danger if action == "del" else discord.ButtonStyle.primary
        emoji = {"edit": "✏️", "del": "🗑️"}.get(action)
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, row=row, custom_id=f"kb:{eid}:{action}"))
        self.eid, self.action = eid, action

    @classmethod
    async def from_custom_id(cls, it, item, match, /):
        return cls(int(match["id"]), match["a"])

    async def callback(self, it: discord.Interaction):
        x = get(self.eid)
        if not x:
            return await it.response.send_message("見つかりません", ephemeral=True)
        if self.action == "edit":
            return await it.response.send_modal(EditModal(x))
        if self.action == "del":
            db.run("delete from expenses where id=?", self.eid)
            search_index.drop("receipt", self.eid)
            return await it.response.edit_message(content=f"🗑️ #{self.eid} を取り消しました", embed=None, view=None)
        cat = config.CATEGORIES[int(self.action[1:])]
        db.run("update expenses set category=? where id=?", cat, self.eid)
        k = key_of(x["store"]) or key_of(x["memo"])
        if k:  # 覚えて、次から同じ店・メモに自動で付ける
            db.run("insert or replace into store_cat values(?, ?)", k, cat)
        x = get(self.eid)
        index(x, it.message.jump_url)
        await it.response.edit_message(embed=embed(x), view=view(x))


class EditModal(discord.ui.Modal, title="支出を直す"):
    def __init__(self, x):
        super().__init__()
        self.eid = x["id"]
        self.amount = discord.ui.TextInput(label="金額", default=str(x["amount"] or ""), required=False)
        self.day = discord.ui.TextInput(label="日付（2026-11-13 や 11/13）", default=x["day"])
        self.store = discord.ui.TextInput(label="店", default=x["store"] or "", required=False)
        self.memo = discord.ui.TextInput(label="メモ", default=x["memo"] or "", required=False)
        for f in (self.amount, self.day, self.store, self.memo):
            self.add_item(f)

    async def on_submit(self, it: discord.Interaction):
        a = re.sub(r"[^\d]", "", unicodedata.normalize("NFKC", self.amount.value))
        d = unicodedata.normalize("NFKC", self.day.value).strip()
        m = re.match(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})$", d)
        try:
            day = dt.date(int(m.group(1) or config.today().year), int(m.group(2)), int(m.group(3))).isoformat()
        except (AttributeError, ValueError):
            return await it.response.send_message("日付を読み取れませんでした", ephemeral=True)
        db.run("update expenses set amount=?, day=?, store=?, memo=? where id=?",
               int(a) if a else None, day, self.store.value or None, self.memo.value or None, self.eid)
        x = get(self.eid)
        if not x["category"] and (c := guess(x["store"], x["memo"])):
            db.run("update expenses set category=? where id=?", c, self.eid)
            x = get(self.eid)
        index(x, it.message.jump_url)
        await it.response.edit_message(embed=embed(x), view=view(x))


class Kakeibo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(ExpenseButton)

    async def record(self, msg, day, amount, store, memo, image=None, ocr=None):
        eid = db.run("insert into expenses(day, amount, category, store, memo, image, ocr) values(?,?,?,?,?,?,?)",
                     day.isoformat(), amount, guess(store, memo), store, memo, image, ocr)
        x = get(eid)
        r = await msg.reply(embed=embed(x), view=view(x))
        index(x, r.jump_url)

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.channel.id != config.KAKEIBO or msg.author.bot:
            return
        images = [a for a in msg.attachments if a.filename.lower().endswith(IMAGE_EXT)]
        day = msg.created_at.astimezone(config.JST).date()
        for i, a in enumerate(images):
            data = await a.read()
            folder = config.DATA / "receipts" / day.strftime("%Y-%m")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{msg.id}_{i}{a.filename[a.filename.rfind('.'):].lower()}"
            path.write_bytes(data)
            async with msg.channel.typing():
                try:
                    text = await asyncio.to_thread(receipt.ocr, data, config.OCR_LANG)
                except Exception as ex:  # Tesseract が入っていない、HEIC が開けない など
                    text = ""
                    await msg.reply(f"文字を読めませんでした（{type(ex).__name__}: {ex}）。✏️ で入力してください")
            r = receipt.parse(text)
            await self.record(msg, r["date"] or day, r["amount"], r["store"], msg.content or None, str(path), text)
        if not images and (m := receipt.parse_manual(msg.content)):
            await self.record(msg, day, m[0], None, m[1] or None)
        elif not images:
            await msg.reply("金額を読み取れませんでした（例：`680 昼食`、またはレシートの写真）", delete_after=10)

    @app_commands.command(name="kakeibo", description="ひと月の集計（CSV つき）")
    @app_commands.describe(month="2026-11 の形。空なら今月")
    async def kakeibo(self, it: discord.Interaction, month: str = ""):
        month = month or config.today().strftime("%Y-%m")
        rows = db.q("select * from expenses where day like ? order by day, id", f"{month}%")
        if not rows:
            return await it.response.send_message(f"{month} の記録はありません", ephemeral=True)
        by = {}
        for r in rows:
            by[r["category"] or "未分類"] = by.get(r["category"] or "未分類", 0) + (r["amount"] or 0)
        total = sum(by.values())
        e = discord.Embed(title=f"🧾 {month}　合計 ¥{total:,}（{len(rows)} 件）", color=0xF0B232)
        e.description = "\n".join(f"{c}：¥{v:,}（{v / total:.0%}）" if total else f"{c}：¥{v:,}"
                                  for c, v in sorted(by.items(), key=lambda x: -x[1]))
        missing = sum(1 for r in rows if not r["amount"])
        if missing:
            e.set_footer(text=f"金額のない記録が {missing} 件あります")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["日付", "金額", "分類", "店", "メモ", "写真"])
        for r in rows:
            w.writerow([r["day"], r["amount"], r["category"], r["store"], r["memo"], r["image"]])
        f = discord.File(io.BytesIO(buf.getvalue().encode("utf-8-sig")), f"kakeibo-{month}.csv")  # Excel で文字化けしない
        await it.response.send_message(embed=e, file=f)


async def setup(bot):
    await bot.add_cog(Kakeibo(bot))
