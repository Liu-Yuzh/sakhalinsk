# util.py : 複数の cog で使う小さな道具
import discord


def reply(content=None, embed=None, file=None, files=None):
    """None を渡さないための送信用 kwargs（followup.send は file=None で失敗する）"""
    d = {"content": content, "embed": embed, "file": file, "files": files}
    return {k: v for k, v in d.items() if v is not None}


def chunks(text, size=1900):
    """Discord の文字数制限に合わせて、段落の切れ目で分ける"""
    out, cur = [], ""
    for para in text.split("\n"):
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        if len(cur) + len(para) + 1 > size:
            out.append(cur)
            cur = ""
        cur += para + "\n"
    return [c for c in out + [cur] if c.strip()]


def md_links(cs, titles):
    return "\n".join(f"・[{t}]({cs.page_url(t)})" for t in titles)


async def channel(bot, cid):
    return bot.get_channel(cid) or await bot.fetch_channel(cid)


def disabled(view):
    for item in view.children:
        item.disabled = True
    return view
