# cosense.py : Cosense（YuzhNote）の読み書きと、ページ本文の組み立て
#   書き込みは yuzhnote/userscript/yuzh-common/page-io.js と同じ「インポート機能」を使う
import datetime
import json
import re
from urllib.parse import quote, urlencode

import aiohttp

BASE = "https://scrapbox.io"
JST = datetime.timezone(datetime.timedelta(hours=9))
PAPER_TAG, UNREAD = "論文メモ", "積読"
STATUSES = ["積読", "読書中", "読了"]


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def page_title(p):
    return re.sub(r"\s+", " ", p["title"]).strip()


def _link(s):
    return f"[{s.replace('[', '').replace(']', '')}]" if s else ""


def paper_lines(p):
    """論文ページの本文（1行目のタイトルは含まない）"""
    authors = " ".join(_link(a) for a in p["authors"][:10]) + (" ほか" if len(p["authors"]) > 10 else "")
    lines = [f"#{PAPER_TAG} #{UNREAD}", p["url"], f"著者：{authors}",
             f"発表：{p['year']} {_link(p.get('venue', ''))}".rstrip(), f"登録日：{today()}", ""]
    if p.get("tldr"):
        lines += ["[** TLDR]", f">{p['tldr']}", ""]
    if p.get("abstract"):
        lines += ["[** 概要]", f">{p['abstract']}", ""]
    return lines + ["[** 要点]", "", "[** 自分の研究との関係]", ""]


def web_lines(p):
    """メモ送信エディタの「知識メモ」と同じ形"""
    return ["#知識メモ", p["url"], f"見た日：{today()}", "", "[** メモ]", ""]


def set_status(lines, status):
    """本文（1行目のタイトルを含む）の #積読 #読書中 #読了 を付け替える（yuzh-bookshelf/status.js と同じ考え方）"""
    tag = re.compile(rf"(^|\s)#{PAPER_TAG}(?=\s|$)")
    out = [lines[0]]
    for l in lines[1:]:
        t = l
        for s in STATUSES:
            t = re.sub(rf"(^|\s)#{s}(?=\s|$)", r"\1", t)
        t = re.sub(r"[ \t]{2,}", " ", t).rstrip()
        if t != l and not t.strip():
            continue  # 状態のタグだけの行は、行ごと消す
        out.append(t if t != l else l)
    at = next((i for i, l in enumerate(out) if i and tag.search(l)), None)
    if at is None:
        out.insert(1, f"#{status}")
        at = 1
    else:
        out[at] = tag.sub(rf"\1#{PAPER_TAG} #{status}", out[at], count=1)
    if status == "読了" and not any(l.startswith("読了日：") for l in out):
        reg = next((i for i, l in enumerate(out) if l.startswith("登録日：")), at)
        out.insert(max(reg, at) + 1, f"読了日：{today()}")
    return out


def editor_url(editor, p):
    """メモ送信エディタ（share.html の ?url=&title= の仕組み）で開くリンク"""
    return f"{editor}?{urlencode({'url': p['url'], 'title': p['title']})}"


class Cosense:
    def __init__(self, session, project, sid="", write=False):
        self.s, self.project = session, project
        self.headers = {"Cookie": f"connect.sid={sid}"} if sid else {}
        self.can_write = bool(sid) and write

    def page_url(self, title, lines=None):
        u = f"{BASE}/{self.project}/{quote(title, safe='')}"
        return u + (f"?body={quote(chr(10).join(lines), safe='')}" if lines else "")

    async def _get(self, path, **params):
        async with self.s.get(f"{BASE}/api{path}", params=params, headers=self.headers) as r:
            if r.status == 404:
                return None
            if r.status in (401, 403):
                raise PermissionError("非公開プロジェクトを読むには COSENSE_SID が必要です")
            r.raise_for_status()
            return await r.json()

    async def exists(self, title):
        d = await self._get(f"/pages/{self.project}/{quote(title, safe='')}")
        return bool(d and d.get("persistent"))

    async def search(self, q):
        d = await self._get(f"/pages/{self.project}/search/query", q=q)
        return [p["title"] for p in (d or {}).get("pages", [])]

    async def tagged(self, tag, status):
        """tag のページから1ホップのページのうち、status のリンクも持つもの（本棚と同じ考え方）"""
        # shortcut: links1hop は件数に上限がある。数百件を超えたら検索 API で引き直す
        d = await self._get(f"/pages/{self.project}/{quote(tag, safe='')}")
        rel = ((d or {}).get("relatedPages") or {}).get("links1hop", [])
        return [p["title"] for p in rel if status.lower() in p.get("linksLc", [])]

    async def list_pages(self):
        """[(タイトル, 更新時刻), ...]（プロジェクトの全ページ）"""
        out, skip = [], 0
        while True:
            d = await self._get(f"/pages/{self.project}", limit=1000, skip=skip, sort="updated")
            pages = (d or {}).get("pages", [])
            out += [(p["title"], p["updated"]) for p in pages]
            skip += len(pages)
            if not pages or skip >= d.get("count", 0):
                return out

    async def text(self, title):
        async with self.s.get(f"{BASE}/api/pages/{self.project}/{quote(title, safe='')}/text",
                              headers=self.headers) as r:
            if r.status in (401, 403):
                raise PermissionError("非公開プロジェクトを読むには COSENSE_SID が必要です")
            return await r.text() if r.status == 200 else None

    async def lines(self, title):
        d = await self._get(f"/pages/{self.project}/{quote(title, safe='')}")
        return [l["text"] for l in d["lines"]] if d and d.get("persistent") else None

    async def create(self, title, lines):
        # インポートは同名のページを上書きするので、あれば書かない
        if await self.exists(title):
            raise FileExistsError(title)
        await self._import(title, [title, *lines])

    async def update(self, title, change):
        """今のページの行を change(行) で書き換えて保存する
        shortcut: 読んでから書くまでの数秒にブラウザで編集した分は消える。困ったら page-io.js の waitSynced のような確認を足す"""
        lines = await self.lines(title)
        if lines is None:
            raise FileNotFoundError(title)
        new = change(lines)
        if new != lines:
            await self._import(title, new)

    async def _csrf(self):
        """書き込み用の鍵。/api/users/me になければ、ブラウザと同じくページの HTML（window._csrf）から探す"""
        async with self.s.get(f"{BASE}/api/users/me", headers=self.headers) as r:
            r.raise_for_status()
            me = await r.json()
        if me.get("csrfToken"):
            return me["csrfToken"]
        if me.get("isGuest"):
            raise PermissionError("Cosense から見て未ログインです。COSENSE_SID を取り直してください")
        async with self.s.get(f"{BASE}/{self.project}/", headers=self.headers) as r:
            html = await r.text()
        m = re.search(r"""_csrf\s*=\s*["']([^"']+)""", html) or re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)
        raise PermissionError(f"{me.get('name')} としてログインしていますが、書き込み用の鍵（csrfToken）が見つかりませんでした")

    async def _import(self, title, lines):
        csrf = await self._csrf()
        form = aiohttp.FormData()
