# sources.py : URL・arXiv・DOI から書誌を取る（LLM は使わない。すべて無料の公開 API）
import asyncio
import html
import re
import xml.etree.ElementTree as ET

import aiohttp

UA = {"User-Agent": "Sakhalinsk/0.1 (personal research notes)"}
# shortcut: 旧形式の arXiv ID（hep-th/9901001 など）は未対応。使う分野で必要になったら足す
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s?#>]+)", re.I)
URL_RE = re.compile(r"https?://\S+")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
SHORT = aiohttp.ClientTimeout(total=10)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_arxiv_feed(xml_text):
    """arXiv API の Atom を論文の dict の一覧にする"""
    out = []
    for e in ET.fromstring(xml_text).findall("a:entry", ATOM):
        m = ARXIV_RE.search(e.findtext("a:id", "", ATOM))
        if not m:  # エラー時の entry はここで落ちる
            continue
        out.append({
            "kind": "arxiv", "id": m.group(1),
            "title": clean(e.findtext("a:title", "", ATOM)),
            "authors": [clean(a.findtext("a:name", "", ATOM)) for a in e.findall("a:author", ATOM)],
            "abstract": clean(e.findtext("a:summary", "", ATOM)),
            "year": e.findtext("a:published", "", ATOM)[:4],
            "venue": "arXiv",
            "url": f"https://arxiv.org/abs/{m.group(1)}",
        })
    return out


async def _arxiv(s, params):
    async with s.get("https://export.arxiv.org/api/query", params=params, headers=UA) as r:
        r.raise_for_status()
        return parse_arxiv_feed(await r.text())


async def arxiv_by_id(s, aid):
    items = await _arxiv(s, {"id_list": aid})
    return items[0] if items else None


async def arxiv_latest(s, cats, n=100):
    q = " OR ".join(f"cat:{c}" for c in cats)
    return await _arxiv(s, {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": n})


async def crossref(s, doi):
    async with s.get(f"https://api.crossref.org/works/{doi}", headers=UA) as r:
        if r.status != 200:
            return None
        m = (await r.json())["message"]
    year = ((m.get("issued", {}).get("date-parts") or [[None]])[0] or [None])[0]
    return {
        "kind": "doi", "id": doi,
        "title": clean((m.get("title") or [""])[0]),
        "authors": [clean(f"{a.get('given', '')} {a.get('family', a.get('name', ''))}") for a in m.get("author", [])],
        "abstract": clean(re.sub(r"<[^>]+>", " ", m.get("abstract", ""))),  # Crossref の要旨は JATS のタグ付き
        "year": str(year or ""),
        "venue": clean((m.get("container-title") or [""])[0]),
        "url": f"https://doi.org/{doi}",
    }


async def add_s2(s, p):
    """Semantic Scholar の TLDR（1文要約）と被引用数を足す。取れなくても続ける"""
    key = f"arXiv:{p['id']}" if p["kind"] == "arxiv" else f"DOI:{p['id']}"
    try:
        async with s.get(f"https://api.semanticscholar.org/graph/v1/paper/{key}",
                         params={"fields": "tldr,venue,citationCount"}, headers=UA, timeout=SHORT) as r:
            if r.status != 200:  # 鍵なしは混むと 429 になる
                return
            d = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return
    p["tldr"] = (d.get("tldr") or {}).get("text") or ""
    if d.get("venue"):
        p["venue"] = d["venue"]
    p["citations"] = d.get("citationCount")


async def web_page(s, url):
    title = ""
    try:
        async with s.get(url, headers=UA, timeout=SHORT) as r:
            if "html" in r.headers.get("Content-Type", ""):
                text = (await r.content.read(300_000)).decode(r.charset or "utf-8", "replace")
                # shortcut: og:title は property→content の順の書き方だけ見る。崩れたら <title> を使う
                m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', text, re.I)
                     or re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S))
                if m:
                    title = clean(html.unescape(m.group(1)))
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
        pass
    return {"kind": "web", "url": url, "title": title or url}


async def resolve(s, text):
    """メッセージの文字列から、論文か Web ページの情報を作る。何もなければ None"""
    if m := ARXIV_RE.search(text):
        p = await arxiv_by_id(s, m.group(1))
    elif m := DOI_RE.search(text):
        p = await crossref(s, m.group(1).rstrip(".,);"))
    elif m := URL_RE.search(text):
        return await web_page(s, m.group(0).rstrip(">)"))
    else:
        return None
    if p:
        await add_s2(s, p)
    return p


KINDLE_DEALS = "https://www.amazon.co.jp/kindle-dbs/browse?widgetId=ebooks-deals-storefront_KindleDailyDealsStrategy&sourceType=recs"
_DEAL_RE = re.compile(r'<a aria-label="([^"]+)" class="a-link-normal" href="/gp/product/(B[0-9A-Z]{9})')
_PRICE_RE = re.compile(r'a-color-price[^>]*>\s*(￥\s*[\d,]+)')


def parse_kindle_deals(page):
    """Kindle 日替わりセールのページから (書名, URL, 価格) を取る"""
    out = []
    # shortcut: Amazon のページの HTML に頼るので、作りが変わると 0 件になる。そうなったら正規表現を直す
    for block in page.split("browse-clickable-item")[1:]:
        m = _DEAL_RE.search(block)
        if m:
            price = _PRICE_RE.search(block)
            out.append((html.unescape(m.group(1)), f"https://www.amazon.co.jp/dp/{m.group(2)}",
                        price.group(1).replace(" ", "") if price else ""))
    return out


async def kindle_deals(s):
    ua = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0 Safari/537.36", "Accept-Language": "ja"}  # Bot の UA だと弾かれる
    async with s.get(KINDLE_DEALS, headers=ua) as r:
        r.raise_for_status()
        return parse_kindle_deals(await r.text())
