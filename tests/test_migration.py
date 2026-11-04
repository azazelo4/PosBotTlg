"""Миграция существующей БД со старой схемой: данные сохраняются, новые операции работают."""
import os

import mysql.connector

from schema import ensure_schema

LEGACY_DB = "bot_legacy_test"


def _connect(database=None):
    return mysql.connector.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                                   password=os.environ["DB_PASS"], database=database, charset="utf8mb4",
                                   collation="utf8mb4_general_ci")


def _columns(cur, table):
    cur.execute("SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (LEGACY_DB, table))
    return dict(cur.fetchall())


def test_legacy_database_is_upgraded_in_place(fake):
    root = _connect()
    cur = root.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {LEGACY_DB}")
    cur.execute(f"CREATE DATABASE {LEGACY_DB} CHARACTER SET utf8mb4")
    root.close()

    conn = _connect(LEGACY_DB)
    cur = conn.cursor()
    sql = open(os.path.join(os.path.dirname(__file__), "legacy_schema.sql"), encoding="utf-8").read()
    for statement in sql.split(";\n"):
        if statement.strip() and not all(l.strip().startswith("--") or not l.strip() for l in statement.splitlines()):
            cur.execute(statement)
    conn.commit()

    ensure_schema(conn)
    ensure_schema(conn)  # повторный запуск ничего не ломает

    assert {'size', 'track_stock'} <= set(_columns(cur, 'products'))
    assert _columns(cur, 'products')['manufacturer'] == 'YES'
    assert 'chat_id' in _columns(cur, 'users')
    assert {'description', 'metal_type', 'weight'} <= set(_columns(cur, 'buybacks'))
    assert _columns(cur, 'buybacks')['product_article'] == 'YES'
    assert _columns(cur, 'returns')['store_id'] == 'YES'
    assert {'old_item_value', 'new_sale_price'} <= set(_columns(cur, 'trade_ins'))

    # старые данные на месте
    cur.execute("SELECT model, quantity, manufacturer FROM products WHERE article_number='OLD1'")
    assert cur.fetchone() == ('Старое кольцо', 2, 'Завод')
    cur.execute("SELECT COUNT(*) FROM sales")
    assert cur.fetchone()[0] == 1

    # новые операции проходят на старой БД (store_id и артикул могут быть пустыми)
    cur.execute("INSERT INTO buybacks (product_article, description, metal_type, weight, buyback_price, payment_type, user_id, store_id, buyback_date)"
                " VALUES (NULL, 'лом', 'золото', 1.5, 30000, 'Kaspi', 1, NULL, NOW())")
    cur.execute("INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)"
                " VALUES (1, 'OLD1', 95000, 'Наличные', 1, NULL, NOW())")
    cur.execute("INSERT INTO products (article_number, Store, metal_type, product_type, model, weight, price, min_price, arrival_date, quantity)"
                " VALUES ('NEW1', 'ЦУМ', 'золото', 'цепь', 'Новая', 3, 1, 1, CURDATE(), 1)")
    conn.commit()
    conn.close()

    root = _connect()
    root.cursor().execute(f"DROP DATABASE {LEGACY_DB}")
    root.close()
