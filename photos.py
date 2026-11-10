"""Фото товаров (#24): необязательные, одно или несколько, первое — главное.

Файлы лежат в томе DATA_DIR/photos/<product_id>/<имя>.jpg (+ превью <имя>_t.jpg), в БД — только имя и порядок.
При загрузке картинка пересжимается: длинная сторона до 1600 px, превью 320 px, JPEG без EXIF
(в EXIF бывает геопозиция). Тип проверяется по содержимому (Pillow), а не по расширению.

Публичной папки нет: веб отдаёт фото по ссылке с подписью и сроком (signed_url) — так их видят и админка,
и касса на ПК, и Mini App, где картинку нельзя запросить с заголовком авторизации.
"""
import hashlib
import hmac
import io
import logging
import os
import secrets
import time
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import config
from database import with_connection, with_transaction, BusinessError
from utils.common import now

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_PRODUCT = 10
FULL_SIDE, THUMB_SIDE = 1600, 320
MAX_PIXELS = 50_000_000                    # защита от «бомб»: 50 Мп хватит любому телефону
FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "BMP", "MPO")
URL_TTL = 12 * 3600


def root():
    return Path(config.DATA_DIR) / "photos"


def _dir(product_id):
    return root() / str(int(product_id))


def file_path(product_id, name, thumb=False):
    return _dir(product_id) / (f"{name}_t.jpg" if thumb else f"{name}.jpg")


# ------------------------------------------------------------------ обработка картинки

def _resized(img, side, quality):
    copy = img.copy()
    copy.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    copy.save(out, "JPEG", quality=quality, optimize=True, progressive=True)   # без exif=… — метаданные не пишутся
    return out.getvalue()


def prepare(data):
    """Байты загруженного файла → (полное, превью) JPEG. BusinessError, если это не картинка или она слишком большая."""
    if not data:
        raise BusinessError("Пустой файл.")
    if len(data) > MAX_BYTES:
        raise BusinessError(f"Фото больше {MAX_BYTES // 1024 // 1024} МБ — уменьшите его.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in FORMATS:
                raise BusinessError("Это не фото: подходят JPEG, PNG, WebP.")
            if probe.width * probe.height > MAX_PIXELS:
                raise BusinessError("Слишком большое разрешение фото.")
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except BusinessError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise BusinessError("Это не фото: подходят JPEG, PNG, WebP.")
    img = ImageOps.exif_transpose(img)                 # поворот как на телефоне, до удаления EXIF
    if img.mode not in ("RGB", "L"):
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, (255, 255, 255))
        img.paste(rgba, mask=rgba.getchannel("A"))
    elif img.mode == "L":
        img = img.convert("RGB")
    return _resized(img, FULL_SIDE, 85), _resized(img, THUMB_SIDE, 80)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _remove_files(product_id, name):
    for thumb in (False, True):
        try:
            file_path(product_id, name, thumb).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Не удалось удалить фото %s/%s: %s", product_id, name, e)


# ------------------------------------------------------------------ БД

@with_transaction
def _insert(cursor, product_id, name, user_id):
    cursor.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (product_id,))
    if not cursor.fetchone():
        raise BusinessError("Товар не найден.")
    cursor.execute("SELECT COUNT(*) AS n, COALESCE(MAX(sort_order), -1) AS last FROM product_photos WHERE product_id = %s",
                   (product_id,))
    row = cursor.fetchone()
    if row['n'] >= MAX_PER_PRODUCT:
        raise BusinessError(f"У товара уже {MAX_PER_PRODUCT} фото — удалите лишние.")
    cursor.execute("""INSERT INTO product_photos (product_id, file, sort_order, created_at, created_by)
                      VALUES (%s, %s, %s, %s, %s)""", (product_id, name, row['last'] + 1, now(), user_id))
    return True, cursor.lastrowid


def add(product_id, data, user_id=None):
    """Сохранить фото к товару. (True, id фото) или (False, причина)."""
    try:
        full, thumb = prepare(data)
    except BusinessError as e:
        return False, str(e)
    name = secrets.token_hex(8)
    try:
        _write(file_path(product_id, name), full)
        _write(file_path(product_id, name, True), thumb)
    except OSError as e:
        logger.error("Не удалось записать фото товара %s: %s", product_id, e)
        _remove_files(product_id, name)
        return False, "Не удалось сохранить фото на диск. Проверьте свободное место."
    ok, result = _insert(product_id, name, user_id)
    if not ok:
        _remove_files(product_id, name)
    return ok, result


@with_connection
def for_product(cursor, product_id):
    cursor.execute("SELECT * FROM product_photos WHERE product_id = %s ORDER BY sort_order, id", (product_id,))
    return cursor.fetchall()


@with_connection
def main_photos(cursor, product_ids):
    """{product_id: строка главного фото} для списка товаров."""
    ids = sorted({int(i) for i in product_ids if i})
    if not ids:
        return {}
    marks = ",".join(["%s"] * len(ids))
    cursor.execute(f"""SELECT p.* FROM product_photos p
                       JOIN (SELECT product_id, MIN(sort_order) AS so FROM product_photos
                             WHERE product_id IN ({marks}) GROUP BY product_id) m
                         ON m.product_id = p.product_id AND m.so = p.sort_order
                       ORDER BY p.id""", ids)
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row['product_id'], row)
    return result


@with_connection
def get(cursor, photo_id):
    cursor.execute("SELECT * FROM product_photos WHERE id = %s", (photo_id,))
    return cursor.fetchone()


@with_transaction
def _delete_row(cursor, photo_id):
    cursor.execute("SELECT * FROM product_photos WHERE id = %s FOR UPDATE", (photo_id,))
    row = cursor.fetchone()
    if not row:
        raise BusinessError("Фото не найдено.")
    cursor.execute("DELETE FROM product_photos WHERE id = %s", (photo_id,))
    return True, row


def delete(photo_id):
    ok, row = _delete_row(photo_id)
    if ok:
        _remove_files(row['product_id'], row['file'])
    return ok, row


@with_transaction
def make_main(cursor, photo_id):
    cursor.execute("SELECT product_id FROM product_photos WHERE id = %s", (photo_id,))
    row = cursor.fetchone()
    if not row:
        raise BusinessError("Фото не найдено.")
    cursor.execute("SELECT id FROM product_photos WHERE product_id = %s ORDER BY id = %s DESC, sort_order, id FOR UPDATE",
                   (row['product_id'], photo_id))
    for order, r in enumerate(cursor.fetchall()):
        cursor.execute("UPDATE product_photos SET sort_order = %s WHERE id = %s", (order, r['id']))
    return True, row['product_id']


@with_connection
def disk_usage(cursor):
    """(число фото, байт на диске) — для настроек и статуса."""
    cursor.execute("SELECT COUNT(*) AS n FROM product_photos")
    count = cursor.fetchone()['n']
    size = sum(f.stat().st_size for f in root().rglob("*.jpg")) if root().exists() else 0
    return count, size


# ------------------------------------------------------------------ ссылки с подписью

def _secret():
    from web.security import session_secret
    return session_secret().encode()


def _sign(photo_id, size, exp):
    return hmac.new(_secret(), f"photo:{photo_id}:{size}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def signed_url(photo, thumb=True):
    """Ссылка на фото, действует 12–24 часа (срок округлён, чтобы браузер кэшировал одну и ту же ссылку)."""
    if not photo:
        return None
    size = "t" if thumb else "f"
    exp = (int(time.time()) // URL_TTL + 2) * URL_TTL
    return f"/photos/{photo['id']}/{size}?e={exp}&s={_sign(photo['id'], size, exp)}"


def check_signature(photo_id, size, exp, sig):
    if size not in ("t", "f") or not sig or not exp or exp < time.time():
        return False
    return hmac.compare_digest(_sign(photo_id, size, exp), sig)
