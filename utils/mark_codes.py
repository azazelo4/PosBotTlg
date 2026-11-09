"""Коды маркировки DataMatrix: нормализация и распознавание.

Код хранится как отсканирован (с разделителем GS, он нужен для передачи в ОФД),
а сравнивается по ключу — без управляющих символов и префикса символики сканера.
Формат кода для ювелирных изделий в РК ещё не утверждён, поэтому проверка мягкая.
"""
import re

GS = "\x1d"
MIN_LEN, MAX_LEN = 20, 255
# Префиксы символики, которые добавляют некоторые сканеры: ]d2 (DataMatrix GS1), ]C1 (GS1-128), ]Q3 (QR)
_SYMBOLOGY = re.compile(r"^\][A-Za-z]\d")
_GS_ESCAPES = ("<GS>", "\\x1d", "\\u001d", "{GS}")


def clean(raw):
    """Код как отсканирован, но без пробелов по краям, префикса символики и с GS вместо его записей."""
    code = str(raw or "").strip().strip("\r\n")
    code = _SYMBOLOGY.sub("", code)
    for esc in _GS_ESCAPES:
        code = code.replace(esc, GS)
    return code


def key(raw):
    """Ключ для сравнения: без управляющих символов (сканер может передать GS, а может и нет)."""
    return "".join(ch for ch in clean(raw) if ord(ch) >= 32)


def is_valid(raw):
    k = key(raw)
    return MIN_LEN <= len(k) <= MAX_LEN and all(32 < ord(ch) < 127 for ch in k)


def looks_like_code(text):
    """Похоже на код маркировки, а не на артикул: длинная строка без пробелов."""
    return is_valid(text)


_GS1 = re.compile(r"^01(\d{14})21")


def short(raw):
    """Короткая запись для экрана. Для кода GS1 — GTIN и серийный номер (он и отличает экземпляры)."""
    k = key(raw)
    m = _GS1.match(k)
    if m:
        cleaned = clean(raw)
        tail = cleaned[18:]
        serial = tail.split(GS, 1)[0] if GS in tail else tail[:13]
        return f"{m.group(1)} · {serial}"
    return k if len(k) <= 24 else f"{k[:12]}…{k[-8:]}"


def split_codes(text):
    """Несколько кодов, вставленных в поле: по одному в строке (или через пробел/таб)."""
    return [c for c in re.split(r"[\r\n\t ]+", str(text or "")) if c.strip()]
