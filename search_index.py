# search_index.py : Discord・Cosense・レシートをまとめた全文検索（SQLite FTS5 の trigram、LLM なし）
import re

import db

SOURCES = {"discord": "💬", "cosense": "📝", "receipt": "🧾", "pdf": "📄"}


def put(source, ref, title, body, url, at):
    db.con.execute("delete from docs where source=? and ref=?", (source, str(ref)))
    db.con.execute("insert into docs(title, body, source, ref, url, at) values(?,?,?,?,?,?)",
                   (title, body, source, str(ref), url, at))
    db.con.commit()


def drop(source, ref):
    db.run("delete from docs where source=? and ref=?", source, str(ref))


def refs(source):
    return {r["ref"] for r in db.q("select ref from docs where source=?", source)}


def _snippet(text, terms, width=60):
    low = text.lower()
    i = min((low.find(t.lower()) for t in terms if t.lower() in low), default=0)
    s = text[max(0, i - width // 2): i + width].replace("\n", " ")
    for t in terms:
        s = re.sub(re.escape(t), lambda m: f"**{m.group(0)}**", s, flags=re.I)
    return ("…" if i > width // 2 else "") + s + "…"


def parse_query(text):
    """「@cosense 論文 検索」→ (['論文', '検索'], 'cosense')。"..." で空白を含む語も書ける"""
    source = None
    terms = []
    for t in re.findall(r'"([^"]+)"|(\S+)', text):
        t = t[0] or t[1]
        if t.startswith("@") and t[1:] in SOURCES:
            source = t[1:]
        else:
            terms.append(t)
    return terms, source


def search(text, limit=10):
    terms, source = parse_query(text)
    if not terms:
        return []
    long_ = [t for t in terms if len(t) >= 3]
    short = [t for t in terms if len(t) < 3]
    where, args = [], []
    if long_:
        where.append("docs match ?")
        args.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_))
    for t in short:  # trigram は3文字未満を探せないので LIKE で絞る
        where.append("(title like ? or body like ?)")
        args += [f"%{t}%"] * 2
    if source:
        where.append("source = ?")
        args.append(source)
    order = "bm25(docs, 5.0, 1.0)" if long_ else "at desc"  # タイトルに当たったものを上に
    rows = db.q(f"select title, body, source, ref, url, at from docs where {' and '.join(where)} "
                f"order by {order} limit ?", *args, limit)
    return [dict(r) | {"snippet": _snippet(r["title"] + " " + r["body"], terms)} for r in rows]
