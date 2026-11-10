"""Фото товаров (#24): обработка картинки, админка, касса, бот, импорт из .zip."""
import io
import re
import time
import zipfile

import openpyxl
import pytest
from PIL import Image

import photos
from conftest import MANAGER, CASHIER, login, db_query
from test_kassa import client as kassa_client, kassa_login  # noqa: F401  (фикстура)
from test_web import client, web_login, csrf  # noqa: F401  (фикстура)


def _img(fmt="JPEG", size=(2400, 1200), mode="RGB", exif_gps=False):
    img = Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    out = io.BytesIO()
    kw = {}
    if exif_gps:
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"                      # Make
        exif[0x8825] = {1: "N", 2: (43.0, 15.0, 0.0)}    # GPSInfo
        kw["exif"] = exif
    img.save(out, fmt, **kw)
    return out.getvalue()


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def test_prepare_resizes_strips_exif_and_checks_content():
    full, thumb = photos.prepare(_img(exif_gps=True))
    big, small = Image.open(io.BytesIO(full)), Image.open(io.BytesIO(thumb))
    assert big.format == "JPEG" and big.size == (1600, 800) and small.size == (320, 160)
    assert not big.getexif() and "exif" not in big.info
    full, _ = photos.prepare(_img("PNG", (500, 400), "RGBA"))           # прозрачность → белый фон
    assert Image.open(io.BytesIO(full)).mode == "RGB"
    for bad in (b"not an image", b"\xff\xd8\xff\xe0" + b"0" * 100, b"%PDF-1.7 fake.jpg"):
        with pytest.raises(photos.BusinessError, match="Это не фото"):
            photos.prepare(bad)
    with pytest.raises(photos.BusinessError, match="больше 10 МБ"):
        photos.prepare(b"\xff" * (photos.MAX_BYTES + 1))


def test_add_order_main_delete_and_limit(tg):
    pid = _pid("A100")
    ok, first = photos.add(pid, _img())
    ok2, second = photos.add(pid, _img("PNG", (300, 300)))
    assert ok and ok2
    assert [p['id'] for p in photos.for_product(pid)] == [first, second]
    assert photos.main_photos([pid])[pid]['id'] == first
    photos.make_main(second)
    assert photos.main_photos([pid])[pid]['id'] == second
    row = photos.get(first)
    assert photos.file_path(pid, row['file']).is_file() and photos.file_path(pid, row['file'], True).is_file()
    photos.delete(first)
    assert not photos.file_path(pid, row['file']).exists() and photos.get(first) is None
    for _ in range(photos.MAX_PER_PRODUCT - 1):
        assert photos.add(pid, _img("PNG", (50, 50)))[0]
    ok, msg = photos.add(pid, _img("PNG", (50, 50)))
    assert not ok and "уже 10 фото" in msg
    assert photos.add(999999, _img())[1] == "Товар не найден."


def test_signed_urls(tg, client):  # noqa: F811
    ok, pid = photos.add(_pid("A100"), _img())
    url = photos.signed_url(photos.get(pid))
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert "private" in r.headers["cache-control"]
    assert client.get(url.replace("/t?", "/f?")).status_code == 403          # подпись привязана к размеру
    assert client.get(url[:-4] + "0000").status_code == 403
    exp = int(time.time()) - 10
    assert client.get(f"/photos/{pid}/t?e={exp}&s={photos._sign(pid, 't', exp)}").status_code == 403
    assert client.get(photos.signed_url(photos.get(pid), thumb=False)).status_code == 200


def test_web_upload_gallery_and_list(tg, client):  # noqa: F811
    web_login(tg, client, MANAGER)
    pid = _pid("A100")
    form = client.get(f"/products/{pid}").text
    assert 'enctype="multipart/form-data"' in form and 'name="photos"' in form
    data = {k: v for k, v in re.findall(r'<input name="(\w+)" value="([^"]*)"', form)}
    data.update(csrf=csrf(client, f"/products/{pid}"), store_id="1", product_type="кольцо", metal_type="золото",
                price="150000", min_price="120000", quantity="3")
    r = client.post(f"/products/{pid}", data=data,
                    files=[("photos", ("a.jpg", _img(), "image/jpeg")), ("photos", ("b.png", _img("PNG", (400, 400)), "image/png")),
                           ("photos", ("virus.jpg", b"MZ\x90\x00 not image", "image/jpeg"))])
    assert r.status_code == 200
    assert "Добавлено фото: 2" in r.text and "virus.jpg" in r.text and "Это не фото" in r.text
    assert "Фото товара" in r.text and "главное" in r.text and r.text.count('href="/photos/') == 2
    gallery = photos.for_product(pid)
    second = gallery[1]['id']
    r = client.post(f"/products/{pid}/photos/{second}", data={"csrf": csrf(client, f"/products/{pid}"), "action": "main"})
    assert "Главное фото выбрано" in r.text and photos.main_photos([pid])[pid]['id'] == second
    listing = client.get("/products").text
    assert listing.count('src="/photos/') == 1
    r = client.post(f"/products/{pid}/photos/{second}", data={"csrf": csrf(client, f"/products/{pid}"), "action": "delete"})
    assert "Фото удалено" in r.text and len(photos.for_product(pid)) == 1
    # фото чужого товара по адресу этого — 404
    other = photos.add(_pid("A200"), _img())[1]
    r = client.post(f"/products/{pid}/photos/{other}", data={"csrf": csrf(client, f"/products/{pid}"), "action": "delete"})
    assert r.status_code == 404 and photos.get(other)


def test_new_product_with_photo(tg, client):  # noqa: F811
    web_login(tg, client, MANAGER)
    data = {"csrf": csrf(client, "/products/new"), "article_number": "NEW1", "store_id": "1", "model": "Кольцо Новое",
            "product_type": "кольцо", "metal_type": "золото", "price": "100000", "min_price": "90000", "quantity": "1"}
    r = client.post("/products/new", data=data, files=[("photos", ("n.jpg", _img(), "image/jpeg"))])
    assert "Добавлено фото: 1" in r.text and len(photos.for_product(_pid("NEW1"))) == 1


def test_kassa_shows_photo(tg, kassa_client):  # noqa: F811
    kassa_login(tg, kassa_client)
    assert "photo" not in kassa_client.get("/kassa/api/product", params={"article": "A100"}).json()
    photos.add(_pid("A100"), _img())
    p = kassa_client.get("/kassa/api/product", params={"article": "A100"}).json()
    assert p["photo"].startswith("/photos/") and "/f?" in p["photo_full"]
    assert kassa_client.get(p["photo"]).status_code == 200
    assert "photoThumb" in kassa_client.get("/kassa").text


def test_bot_photo_after_add_and_by_article(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Склад")
    calls = tg.send(MANAGER, "Фото к товару")
    assert "Введите артикул" in tg.texts(calls)
    assert "не найден" in tg.texts(tg.send(MANAGER, "NOPE"))
    calls = tg.send(MANAGER, "A100")
    assert "Пришлите фото" in tg.texts(calls) and "Первое станет главным" in tg.texts(calls)
    assert "Фото сохранено (1)" in tg.texts(tg.photo(MANAGER, _img()))
    assert "Это не фото" in tg.texts(tg.photo(MANAGER, b"garbage"))
    assert "Фото сохранено (2)" in tg.texts(tg.photo(MANAGER, _img("PNG", (600, 600))))
    assert "Пришлите фото или нажмите" in tg.texts(tg.send(MANAGER, "что-то"))
    assert "добавлено фото: 2" in tg.texts(tg.send(MANAGER, "Готово"))
    assert len(photos.for_product(_pid("A100"))) == 2
    # кассиру раздел недоступен
    login(tg, CASHIER)
    tg.send(CASHIER, "Фото к товару")
    assert tg.photo(CASHIER, _img()) is not None and len(photos.for_product(_pid("A100"))) == 2


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, data, name="catalog.zip"):  # noqa: F811
    r = client.post("/import", data={"csrf": csrf(client, "/import"), "store": "1"},
                    files={"file": (name, data, "application/zip")})
    assert r.status_code == 200, r.text
    return r


def _apply(client, r):  # noqa: F811
    action = re.search(r'action="(/import/[^"]+)"', r.text).group(1)
    token = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    return client.post(action, data={"csrf": token, "mode": "all"})


def test_import_zip_by_article_and_column(tg, client):  # noqa: F811
    web_login(tg, client, MANAGER)
    photos.add(_pid("A200"), _img())                      # у A200 фото уже есть — из архива не добавится
    table = _xlsx([["Артикул", "Наименование", "Металл", "Цена", "Остаток"],
                   ["Z1", "Серьги Луна", "Серебро 925", 12000, 4],
                   ["A100", "Кольцо Классика", "Золото 585", 150000, 3],
                   ["A200", "Цепь Бисмарк", "Серебро 925", 20000, 1]])
    data = _zip({"каталог/catalog.xlsx": table, "каталог/Z1.jpg": _img(), "каталог/z1_2.png": _img("PNG", (300, 300)),
                 "каталог/A100 (1).JPG": _img(), "каталог/A200.jpg": _img(), "__MACOSX/._Z1.jpg": b"junk",
                 "каталог/A1000.jpg": _img()})
    r = _import(client, data)
    assert "Фото из архива" in r.text and re.search(r"Фото из архива</div><div class=\"stat\">4<", r.text)
    r = _apply(client, r)
    assert "Добавлено фото: 3" in r.text
    assert len(photos.for_product(_pid("Z1"))) == 2 and len(photos.for_product(_pid("A100"))) == 1
    assert len(photos.for_product(_pid("A200"))) == 1
    assert not any((photos.root().parent / "import").iterdir())          # временный архив удалён
    # повторный импорт того же архива дублей не создаёт
    assert "Добавлено фото" not in _apply(client, _import(client, data)).text


def test_import_zip_photo_column_and_errors(tg, client):  # noqa: F811
    web_login(tg, client, MANAGER)
    table = _xlsx([["Артикул", "Наименование", "Металл", "Цена", "Фото"],
                   ["Z2", "Кольцо", "Золото 585", 50000, "front.jpg, side.jpg"]])
    r = _import(client, _zip({"t.xlsx": table, "front.jpg": _img()}))
    assert "Нет в архиве фото" in r.text and "side.jpg" in r.text
    assert "Добавлено фото: 1" in _apply(client, r).text
    assert "нет таблицы" in _import(client, _zip({"a.jpg": _img()})).text
    assert "повреждён" in _import(client, b"PK\x03\x04broken").text
    # без архива колонка «Фото» — подсказка
    r = _import(client, table, "catalog.xlsx")
    assert "загрузите таблицу и фото одним архивом" in r.text
