"""Ценники и импорт каталога."""
import io
import re

import openpyxl
from pypdf import PdfReader

from conftest import ADMIN, MANAGER, CASHIER, login, reply_buttons, db_query


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PALOMA_LIKE = [
    ["Выгрузка товаров", None],                       # мусорная строка над заголовком
    ["Артикул", "Наименование", "Категория", "Материал", "Вес, г", "Размер", "Вставки", "Цена, ₸", "Остаток", "Магазин"],
    ["N1", "Кольцо Нежность", "Кольца", "Золото 585", 1.95, 16.5, "фианит", 98000, 2, "ЦУМ"],
    ["N2", "Цепочка Ромб", "Цепи", "Серебро 925", "4,2", None, None, "15 000", 1, "Отау Сауда"],
    ["N3", "Кулон Сердце", None, "585", 0.8, None, None, 45000, 1, "ЦУМ"],        # тип из названия, металл из пробы
    ["A100", "Кольцо Классика", "Кольцо", "золото", 2.5, 17, None, 155000, 5, "ЦУМ"],  # уже есть — обновится
    ["N5", "Непонятное", "Ваза", "золото", 1, None, None, 1000, 1, "ЦУМ"],              # ошибка: тип
    ["N6", "Кольцо", "кольцо", "золото", 1, None, None, None, 1, "ЦУМ"],                # ошибка: нет цены
    ["N1", "Дубль", "кольцо", "золото", 1, None, None, 1000, 1, "ЦУМ"],                 # ошибка: повтор
]


def test_import_from_paloma_like_excel(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Склад")
    tg.send(MANAGER, "Импорт каталога")
    tg.send(MANAGER, "Магазин указан в файле")
    calls = tg.document(MANAGER, _xlsx(PALOMA_LIKE), "paloma.xlsx")
    text = tg.texts(calls)
    assert "новых 3, уже есть в базе 1" in text, text
    assert "Строк с ошибками (не загрузятся): 3" in text
    assert "тип изделия «Ваза» не распознан" in text and "нет цены" in text and "повторяется" in text
    assert "Загрузить: новые и обновить существующие" in reply_buttons(calls[-1])
    calls = tg.send(MANAGER, "Загрузить: новые и обновить существующие")
    assert "добавлено 3, обновлено 1" in tg.texts(calls)
    rows = {r['article_number']: r for r in db_query("SELECT * FROM products")}
    n1, n2, n3, a100 = rows['N1'], rows['N2'], rows['N3'], rows['A100']
    assert (n1['product_type'], n1['metal_type'], n1['fineness'], n1['size'], n1['inserts']) == ('кольцо', 'золото', '585', '16.5', 'фианит')
    assert n1['min_price'] == n1['price'] == 98000 and n1['quantity'] == 2
    assert (n2['product_type'], n2['metal_type'], n2['fineness'], n2['Store'], n2['price']) == ('цепь', 'серебро', '925', 'Отау Сауда', 15000)
    assert abs(n2['weight'] - 4.2) < 1e-6
    assert (n3['product_type'], n3['metal_type']) == ('подвеска', 'золото')
    # существующий товар: цена и остаток обновлены, минимальная цена не выше цены
    assert a100['price'] == 155000 and a100['quantity'] == 5 and a100['min_price'] == 120000


def test_import_only_new_and_default_store(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Импорт каталога")
    tg.send(ADMIN, "Отау Сауда")
    csv_data = "Артикул;Наименование;Металл;Цена\nA100;Переименовано;;1\nC9;Серьги Капли;золото;50000\nC10;Серьги;;1000\n".encode("cp1251")
    calls = tg.document(ADMIN, csv_data, "items.csv")
    text = tg.texts(calls)
    assert "нет колонки остатка" in text and "новых 1, уже есть в базе 1" in text
    assert "C10: металл не распознан" in text  # новый товар без металла
    tg.send(ADMIN, "Загрузить только новые")
    rows = {r['article_number']: r for r in db_query("SELECT * FROM products")}
    assert rows['A100']['model'] == 'Кольцо Классика' and rows['A100']['price'] == 150000  # не тронут
    assert rows['C9']['Store'] == 'Отау Сауда' and rows['C9']['quantity'] == 0 and rows['C9']['product_type'] == 'серьги'


def test_import_rejects_bad_files(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Импорт каталога")
    tg.send(ADMIN, "ЦУМ")
    assert ".xlsx" in tg.texts(tg.document(ADMIN, b"x", "old.xls"))
    assert "заголовков" in tg.texts(tg.document(ADMIN, _xlsx([["a", "b"], [1, 2]]), "x.xlsx"))
    assert "документом" in tg.texts(tg.send(ADMIN, "привет"))


def test_labels_pdf_by_articles_and_stock(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Ценники")
    tg.send(MANAGER, "По артикулам")
    calls = tg.send(MANAGER, "A100, A200 NOPE")
    assert "Не найдены: NOPE" in tg.texts(calls)
    calls = tg.send(MANAGER, "По количеству на складе")
    assert "Ценников: 4" in tg.texts(calls)  # A100 ×3 + A200 ×1
    pdf = PdfReader(io.BytesIO(tg.documents[-1].data))
    assert len(pdf.pages) == 4
    box = pdf.pages[0].mediabox
    assert round(float(box.width) / 72 * 25.4) == 40 and round(float(box.height) / 72 * 25.4) == 30
    text = pdf.pages[0].extract_text()
    assert "150 000" in text and "Кольцо" in text and "A100" in text

    tg.send(MANAGER, "Ценники")
    tg.send(MANAGER, "Все в наличии")
    tg.send(MANAGER, "ЦУМ")
    tg.send(MANAGER, "По 1 на артикул")
    assert len(PdfReader(io.BytesIO(tg.documents[-1].data)).pages) == 1  # A300 нет в наличии


def test_labels_not_for_cashier(tg):
    login(tg, CASHIER)
    assert "Не понял" in tg.texts(tg.send(CASHIER, "Ценники"))
