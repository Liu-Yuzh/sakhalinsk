# pdftools.py : 論文 PDF から「序論の最後の段落」「結論」「図」を取り出す（PyMuPDF、LLM なし）
#   見出しの見つけ方は書式の推測なので、崩れた PDF では取れないことがある
import re

import pymupdf as fitz

NUM = r"(?:\d{1,2}|[IVX]{1,4})[.．]?"
HEAD_RE = re.compile(rf"^\s*{NUM}\s+(\S.{{0,60}})$")
INTRO_RE = re.compile(r"introduction|はじめに|序論|まえがき", re.I)
CONCL_RE = re.compile(r"conclusions?|concluding|summary and|discussion and conclusion|おわりに|結論|むすび|まとめ", re.I)
END_RE = re.compile(r"^\s*(references|bibliography|acknowledge?ments?|参考文献|謝辞)\s*$", re.I)
CAPTION_RE = re.compile(r"^\s*(?:(?:Figure|Fig\.)\s*(\d+)\s*[:.|]|図\s*(\d+)[\s:：.])", re.I)


def _clean(s):
    s = re.sub(r"-\n(?=[a-z])", "", s)            # 行末のハイフンでの単語の分割を戻す
    s = re.sub(r"(?<=[^\n])\n(?=[^\n])", " ", s)  # 段落内の改行をつなぐ
    s = re.sub(r"(?<=[ぁ-んァ-ン一-龥、。]) (?=[ぁ-んァ-ン一-龥])", "", s)  # 日本語の行のつなぎ目の空白
    return re.sub(r"[ \t]+", " ", s).strip()


def _heading(line):
    """見出しなら見出しの名前を返す（「1 Introduction」「2. 関連研究」「III. METHOD」など）"""
    line = line.strip()
    if END_RE.match(line):
        return line
    m = HEAD_RE.match(line)
    if not m or len(line) > 70 or line.endswith((".", "。", ",")):
        return None
    name = m.group(1)
    if re.match(r"^[\d.,%]", name):  # 「3 4.5%」のような表の数字は見出しにしない
        return None
    return name


def sections(doc):
    """[(見出し, [段落, ...]), ...]。見出しの前の部分は見出し "" に入る"""
    out = [("", [])]
    for page in doc:
        for b in page.get_text("blocks"):
            if b[6] != 0:  # 画像のブロック
                continue
            lines = b[4].strip().split("\n")
            name = _heading(lines[0])
            if name is not None:
                out.append((name, []))
                lines = lines[1:]
            body = _clean("\n".join(lines))
            if not body:
                continue
            paras = out[-1][1]
            # 段組みやページの切れ目で分かれた段落（文の途中で終わり、小文字で続く）はつなぐ
            if paras and paras[-1].endswith("-") and body[0].islower():
                paras[-1] = paras[-1][:-1] + body
            elif paras and not re.search(r"[.。:：!?！？]$", paras[-1]) and re.match(r"[a-zぁ-ん]", body):
                paras[-1] += " " + body
            else:
                paras.append(body)
    return out


def intro_conclusion(doc, intro_paras=2):
    secs = sections(doc)
    intro = next((p for h, p in secs if INTRO_RE.search(h)), [])
    concl = next((p for h, p in secs if CONCL_RE.search(h)), [])
    # 序論は最後の段落（貢献のまとめ）と、contribution を含む段落
    tail = intro[-intro_paras:]
    extra = [p for p in intro[:-intro_paras] if re.search(r"contribution|本研究では|本論文では", p, re.I)]
    return "\n\n".join(extra + tail), "\n\n".join(concl)


def figures(doc, dpi=150, limit=10):
    """キャプション（Figure N / 図N）の上にある図の部分を画像にする。[(番号, PNG のバイト列), ...]"""
    out = []
    for page in doc:
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        graphics = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
        graphics += [fitz.Rect(r) for img in page.get_images(full=True) for r in page.get_image_rects(img[0])]
        graphics = [g for g in graphics if g.width > 3 or g.height > 3]
        for b in blocks:
            m = CAPTION_RE.match(b[4])
            if not m:
                continue
            cap = fitz.Rect(b[:4])
            # 図の上端：キャプションより上にあり、横にかかる最も近い本文の段落（長い文）の下端
            paras = [fitz.Rect(p[:4]) for p in blocks if len(p[4]) > 200 and p[3] <= cap.y0 + 1
                     and p[0] < cap.x1 and p[2] > cap.x0]
            top = max((p.y1 for p in paras), default=page.rect.y0)
            parts = [g for g in graphics if g.y0 >= top - 1 and g.y1 <= cap.y0 + 2 and g.x0 < cap.x1 + 20 and g.x1 > cap.x0 - 20]
            if not parts:
                continue
            area = fitz.Rect(parts[0])
            for g in parts[1:]:
                area |= g
            area |= cap
            area = (area + (-4, -4, 4, 4)) & page.rect
            out.append((int(m.group(1) or m.group(2)), page.get_pixmap(clip=area, dpi=dpi).tobytes("png")))
            if len(out) >= limit:
                return out
    return out


def extract(pdf_bytes):
    """(序論の最後, 結論, 図の一覧)"""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        intro, concl = intro_conclusion(doc)
        return intro, concl, figures(doc)
