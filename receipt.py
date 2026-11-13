# receipt.py : レシート写真の OCR（Tesseract）と、合計金額・日付・店名の読み取り（LLM なし）
import datetime as dt
import io
import re
import unicodedata

from PIL import Image, ImageFilter, ImageOps

LANG = "jpn+eng"
TOTAL_KEYS = ["合計", "お買上", "お買い上げ", "ご利用額", "請求額", "領収金額", "total"]
SKIP_KEYS = ["小計", "預", "釣", "税", "対象", "点数", "値引", "割引", "ポイント", "クーポン"]
MONEY_RE = re.compile(r"[¥\\Y]?\s*(\d{1,3}(?:[,.]\d{3})+|\d{2,7})\s*円?\s*$")
NOT_STORE = re.compile(r"領収|レシート|receipt|tel|電話|〒|http|www|\d{3,}-\d|登録番号|^\W*$", re.I)


def norm(s):
    return unicodedata.normalize("NFKC", s)


def ocr(image_bytes, lang=LANG):
    import pytesseract  # Tesseract がない環境でも、読み取りの関数は試せるように遅れて読む
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))  # スマホ写真の向き
    img = ImageOps.grayscale(img)
    if img.width < 1500:  # 小さい文字は拡大したほうが読める
        img = img.resize((1500, int(img.height * 1500 / img.width)), Image.LANCZOS)
    img = ImageOps.autocontrast(img).filter(ImageFilter.SHARPEN)
    return pytesseract.image_to_string(img, lang=lang, config="--psm 6")


def _amount(line):
    m = MONEY_RE.search(line)
    if not m:
        return None
    v = int(re.sub(r"[,.]", "", m.group(1)))
    return v if 0 < v < 10_000_000 else None


def parse(text):
    """OCR の文字列 → {'amount', 'date', 'store'}（取れないものは None）"""
    lines = [norm(l).strip() for l in text.splitlines() if l.strip()]
    flat = [re.sub(r"\s+", "", l) for l in lines]  # 「合 計」のような空白を消して探す

    amount = None
    for key in TOTAL_KEYS:
        for l, f in zip(lines, flat):
            if key in f.lower() and not any(k in f for k in SKIP_KEYS if k not in key):
                amount = _amount(l) or _amount(f)
                if amount:
                    break
        if amount:
            break
    if amount is None:  # 合計の行が読めないときは、お預りやお釣り以外で最大の金額
        cands = [_amount(l) for l, f in zip(lines, flat)
                 if ("¥" in l or "円" in l or "\\" in l) and not any(k in f for k in SKIP_KEYS)]
        amount = max(filter(None, cands), default=None)

    date = None
    for f in flat:
        m = re.search(r"(20\d{2})[年/.\-](\d{1,2})[月/.\-](\d{1,2})", f) or \
            re.search(r"(?<!\d)(\d{2})[/.\-](\d{1,2})[/.\-](\d{1,2})(?!\d)", f)
        if m:
            y = int(m.group(1))
            try:
                date = dt.date(y if y > 99 else 2000 + y, int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                continue

    store = next((l for l in lines[:6] if not NOT_STORE.search(l)
                  and len(re.findall(r"[ぁ-んァ-ン一-龥A-Za-z]", l)) >= 2 and len(l) <= 30), None)
    return {"amount": amount, "date": date, "store": store}


def parse_manual(text):
    """「680 昼食」「昼食 680円」「1,200 本」→ (金額, メモ)。金額がなければ None"""
    t = norm(text).strip()
    num = r"[¥\\]?(\d{1,3}(?:,\d{3})+|\d+)円?"
    if m := re.match(rf"^{num}(?:\s+(.*))?$", t):
        a, memo = m.group(1), m.group(2) or ""
    elif m := re.match(rf"^(.*?)\s+{num}$", t):
        memo, a = m.group(1), m.group(2)
    else:
        return None
    return int(a.replace(",", "")), memo.strip()
