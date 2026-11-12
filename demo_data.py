"""Демо-данные: пример каталога и операций за две недели, чтобы сразу посмотреть отчёты, кассу и ценники,
и база клиентов с историей покупок за год — для аналитики «кто, сколько и что покупает».

Всё демо помечено и удаляется одной кнопкой (Настройки → Демо-данные):
товары — артикул DEMO-…, операции — по этим товарам, скупка и инкассация — комментарий «Демо: …»,
клиенты — источник согласия «demo» и номера +7 000 000 …, которых не бывает у настоящих людей.
Удаляются и операции, которые сотрудники провели с демо-товарами (пробные продажи, возвраты).
"""
import logging
import random
import secrets
from datetime import timedelta

from database import with_connection, with_transaction, BusinessError
from utils.common import now

logger = logging.getLogger(__name__)

PREFIX = "DEMO-"
MARK = "Демо: "
COMMENT = "Демо-данные — удалить: Настройки → Демо-данные"

GOLD_PER_GRAM = 48000     # ₸ за грамм 585 пробы, розница
SILVER_PER_GRAM = 1400

# тип, название, металл, проба, вес, размер, вставки, производитель, количество
CATALOG = [
    ("кольцо", "Кольцо обручальное классика", "золото", "585", 3.10, "17", None, "Ювелиры Казахстана", 4),
    ("кольцо", "Кольцо с фианитом «Дорожка»", "золото", "585", 2.45, "17.5", "фианит", "Ювелиры Казахстана", 3),
    ("кольцо", "Кольцо с топазом", "золото", "585", 3.80, "18", "топаз", "SOKOLOV", 2),
    ("кольцо", "Кольцо помолвочное", "золото", "750", 2.20, "16.5", "бриллиант 0.10 ct", "SOKOLOV", 1),
    ("серьги", "Серьги-конго", "золото", "585", 2.60, None, None, "Ювелиры Казахстана", 5),
    ("серьги", "Серьги с жемчугом", "золото", "585", 3.15, None, "жемчуг", "Адамас", 3),
    ("серьги", "Серьги «Капли» с фианитами", "серебро", "925", 4.20, None, "фианит", "Серебро Алматы", 8),
    ("детские серьги", "Детские серьги «Бабочки»", "золото", "585", 1.10, None, "эмаль", "Ювелиры Казахстана", 6),
    ("цепь", "Цепь «Бисмарк» 50 см", "золото", "585", 7.80, "50", None, "Адамас", 2),
    ("цепь", "Цепь «Якорная» 45 см", "золото", "585", 3.40, "45", None, "Адамас", 4),
    ("цепь", "Цепь «Снейк» 45 см", "серебро", "925", 6.00, "45", None, "Серебро Алматы", 10),
    ("браслет", "Браслет «Панцирь»", "золото", "585", 5.50, "19", None, "SOKOLOV", 2),
    ("браслет", "Браслет с подвесками", "серебро", "925", 9.30, "18", None, "Серебро Алматы", 5),
    ("подвеска", "Подвеска «Сердце»", "золото", "585", 1.35, None, "фианит", "Ювелиры Казахстана", 7),
    ("подвеска", "Подвеска-крестик", "золото", "585", 0.95, None, None, "Адамас", 6),
    ("подвеска", "Подвеска «Знак зодиака»", "серебро", "925", 2.10, None, None, "Серебро Алматы", 12),
    ("колье", "Колье с изумрудом", "золото", "585", 4.70, "42", "изумруд", "SOKOLOV", 1),
    ("печатка", "Печатка мужская", "золото", "585", 6.90, "21", "оникс", "Ювелиры Казахстана", 2),
    ("брошь", "Брошь «Ветка»", "серебро", "925", 5.40, None, "фианит", "Серебро Алматы", 3),
    ("блезык", "Блезык с орнаментом", "серебро", "925", 18.50, "17", None, "Шебер", 2),
    ("ложка", "Ложка детская «Первый зубик»", "серебро", "925", 12.00, None, None, "Серебро Алматы", 4),
    ("набор", "Набор: серьги и кольцо с аметистом", "серебро", "925", 8.20, "17", "аметист", "Серебро Алматы", 3),
    ("бусы", "Бусы из жемчуга", "бижутерия", None, 28.00, "45", "жемчуг культ.", None, 4),
    ("браслет", "Браслет-шамбала", "бижутерия", None, 15.00, None, "гематит", None, 9),
]

BIJOU_PRICES = {"Бусы из жемчуга": 18900, "Браслет-шамбала": 4500}
PAYMENTS = ["Наличные", "Наличные", "Kaspi", "Kaspi", "Kaspi", "Безналичные"]

CUSTOMER_SOURCE = "demo"
CUSTOMER_PHONE = "+7000000"          # +7 000 000 XX XX — несуществующие номера
HISTORY_DAYS = 365

CUSTOMER_NAMES = [
    "Айгерим Сапарова", "Динара Ахметова", "Асель Нурланова", "Жанар Касымова", "Мадина Омарова", "Алия Бекова",
    "Гульнара Исаева", "Сауле Жумабаева", "Камила Ержанова", "Айжан Тулегенова", "Дана Серикова", "Аружан Маратова",
    "Елена Ковалёва", "Ольга Ким", "Наталья Петренко", "Светлана Цой", "Татьяна Морозова", "Ирина Пак",
    "Анна Шевченко", "Юлия Ли", "Меруерт Абдрахманова", "Зарина Калиева", "Томирис Ахмедова", "Балжан Нуриева",
    "Әсем Қасымова", "Гаухар Мусина", "Жибек Садыкова", "Индира Каримова", "Ляззат Есенова", "Карлыгаш Ибраева",
    "Ерлан Мукашев", "Нурлан Абенов", "Дамир Сейткали", "Арман Жакупов", "Бауыржан Оспанов", "Алексей Иванов",
    "Сергей Ким", "Тимур Алиев", "Ержан Кенжебаев", "Руслан Нам", "Асхат Байтенов", "Марат Утепов",
    "Айнур Бекмуханова", "Венера Галиева", "Роза Шарипова", "Людмила Сидоренко", "Шолпан Аманжолова", "Альбина Хван",
    "Райхан Турсунова", "Салтанат Дюсенова", "Акмарал Рахимова", "Фатима Исмаилова", "Кристина Волкова", None, None,
]
MALE_FIRST = {"Ерлан", "Нурлан", "Дамир", "Арман", "Бауыржан", "Алексей", "Сергей", "Тимур", "Ержан", "Руслан",
              "Асхат", "Марат"}
# Кто как покупает: (сколько клиентов, покупок, давность последней покупки в днях, давность первой)
PERSONAS = [
    ("Постоянные", 8, (4, 8), (2, 45), (200, HISTORY_DAYS)),
    ("Растущие", 8, (2, 2), (3, 80), (40, 220)),
    ("Новые", 9, (1, 1), (0, 25), None),
    ("Разовые", 10, (1, 1), (35, 170), None),
    ("Под угрозой", 8, (2, 4), (95, 170), (200, HISTORY_DAYS)),
    ("Уснувшие", 12, (1, 3), (190, HISTORY_DAYS - 5), (200, HISTORY_DAYS)),
]
NOTES = ["любит серьги", "размер кольца 17", "берёт подарки жене", "предпочитает белое золото", "ждёт новинки с жемчугом",
         "покупает детям", "серебро, без камней", "звонить после 18:00"]


def _price(name, metal, fineness, weight):
    if metal == "бижутерия":
        return BIJOU_PRICES.get(name, 5000), 0
    per_gram = SILVER_PER_GRAM if metal == "серебро" else round(GOLD_PER_GRAM * (1.25 if fineness == "750" else 1))
    return int(round(per_gram * weight * 1.15, -2)), per_gram


@with_connection
def demo_counts(cursor):
    """Сколько демо-записей в базе: {'products': n, 'operations': n, 'customers': n}."""
    cursor.execute("SELECT COUNT(*) AS n FROM products WHERE article_number LIKE %s", (PREFIX + "%",))
    products = cursor.fetchone()['n']
    cursor.execute("""SELECT
        (SELECT COUNT(*) FROM sales s JOIN products p ON p.id = s.product_id WHERE p.article_number LIKE %s)
      + (SELECT COUNT(*) FROM buybacks WHERE description LIKE %s OR product_article LIKE %s)
      + (SELECT COUNT(*) FROM cash_withdrawals WHERE comment LIKE %s) AS n""",
                   (PREFIX + "%", MARK + "%", PREFIX + "%", MARK + "%"))
    operations = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM customers WHERE consent_source = %s", (CUSTOMER_SOURCE,))
    return {"products": products, "operations": operations, "customers": cursor.fetchone()['n']}


def has_demo():
    counts = demo_counts()
    return bool(counts and (counts['products'] or counts['operations'] or counts['customers']))


@with_transaction
def seed_demo(cursor, days=14, seed=None):
    """Добавляет демо-каталог во все активные магазины и операции за последние `days` дней."""
    cursor.execute("SELECT 1 FROM products WHERE article_number LIKE %s LIMIT 1", (PREFIX + "%",))
    if cursor.fetchone():
        raise BusinessError("Демо-данные уже добавлены.")
    cursor.execute("SELECT id, name FROM stores WHERE is_active = 1 ORDER BY sort_order, name")
    stores = cursor.fetchall()
    if not stores:
        raise BusinessError("Сначала добавьте магазин.")
    cursor.execute("SELECT id FROM users WHERE is_active = 1 ORDER BY role = 'admin' DESC, id LIMIT 1")
    user = cursor.fetchone()
    if not user:
        raise BusinessError("Сначала добавьте администратора.")
    user_id = user['id']
    rnd = random.Random(seed)
    current = now()
    start_day = (current - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0)

    # каталог: каждый магазин получает свою партию, артикулы DEMO-<магазин><номер>
    products = []
    for s_index, store in enumerate(stores[:5]):
        for i, (ptype, name, metal, fineness, weight, size, inserts, maker, qty) in enumerate(CATALOG, start=1):
            price, per_gram = _price(name, metal, fineness, weight)
            article = f"{PREFIX}{s_index + 1}{i:02d}"
            arrival = (current - timedelta(days=days + rnd.randint(5, 40))).date()
            min_price = int(round(price * 0.9, -2))
            cursor.execute(
                """INSERT INTO products (article_number, store_id, metal_type, product_type, model, size, fineness,
                       inserts, weight, price_per_gram, price, min_price, arrival_date, quantity, manufacturer,
                       commentaries, track_stock, cost_price)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)""",
                (article, store['id'], metal, ptype, name, size, fineness, inserts, weight, per_gram, price,
                 min_price, arrival, qty + 2, maker, COMMENT, int(round(price * rnd.uniform(0.68, 0.8), -2))))
            products.append({"id": cursor.lastrowid, "article": article, "store_id": store['id'],
                             "price": price, "min_price": min_price, "stock": qty + 2, "metal": metal})

    def moment(day_offset, hour=None):
        """Случайное время рабочего дня; None — если оно ещё не наступило."""
        day = start_day + timedelta(days=day_offset)
        t = day.replace(hour=hour if hour is not None else rnd.randint(10, 19),
                        minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
        return t if t <= current else None

    # продажи: 2–6 в день на магазин, иногда со скидкой; остаток уменьшается
    sales = []
    for store in stores[:5]:
        pool = [p for p in products if p['store_id'] == store['id']]
        for d in range(days):
            for _ in range(rnd.randint(2, 5)):        # чеков в день; в чеке 1–3 позиции
                when = moment(d)
                if when is None:
                    continue
                lines = []
                for _ in range(rnd.choices([1, 2, 3], weights=[70, 22, 8])[0]):
                    candidates = [p for p in pool if p['stock'] > 1]
                    if not candidates:
                        break
                    p = rnd.choice(candidates)
                    price = p['price'] if rnd.random() < 0.7 else max(p['min_price'], int(round(p['price'] * 0.95, -2)))
                    p['stock'] -= 1
                    lines.append((p, price))
                if not lines:
                    break
                payment = rnd.choice(PAYMENTS)
                cursor.execute("""INSERT INTO receipts (store_id, user_id, created_at, total, items_count, payment_type,
                                      public_token) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                               (store['id'], user_id, when, sum(pr for _, pr in lines), len(lines), payment,
                                secrets.token_hex(16)))
                receipt_id = cursor.lastrowid
                for p, price in lines:
                    cursor.execute("""INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type, store_id,
                                          receipt_id, list_price) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                   (p['id'], user_id, when, price, payment, store['id'], receipt_id, p['price']))
                    sales.append({"id": cursor.lastrowid, "product": p, "price": price, "when": when})

        # возврат одной продажи, скупка, инкассация раз в несколько дней
        own = [s for s in sales if s['product']['store_id'] == store['id'] and s['when'].date() < current.date()]
        if own:
            s = rnd.choice(own)
            cursor.execute("""INSERT INTO returns (original_sale_id, product_article, return_price, payment_type,
                                  user_id, store_id, return_date) VALUES (%s, %s, %s, 'Наличные', %s, %s, %s)""",
                           (s['id'], s['product']['article'], s['price'], user_id, store['id'],
                            min(s['when'] + timedelta(days=1), current)))
            s['product']['stock'] += 1
        for d, (metal, weight, what, amount) in zip(
                (2, 6, 10), [("золото", 2.8, "кольцо 585, без камня", 78000),
                             ("золото", 1.6, "серьги 585, лом", 42000),
                             ("серебро", 11.0, "цепь 925", 6500)]):
            when = moment(d) if d < days else None
            if when:
                cursor.execute("""INSERT INTO buybacks (description, metal_type, weight, buyback_price, payment_type,
                                      user_id, store_id, buyback_date) VALUES (%s, %s, %s, %s, 'Наличные', %s, %s, %s)""",
                               (MARK + what, metal, weight, amount, user_id, store['id'], when))
        for d in range(3, days, 4):
            when = moment(d, hour=20)
            if when is None:
                continue
            cursor.execute("""INSERT INTO cash_withdrawals (amount, comment, user_id, store_id, withdrawal_date)
                              VALUES (%s, %s, %s, %s, %s)""",
                           (rnd.choice([150000, 200000, 250000]), MARK + "сдача в банк", user_id, store['id'], when))

    for p in products:
        cursor.execute("UPDATE products SET quantity = %s WHERE id = %s", (p['stock'], p['id']))
    clients = _seed_customers(cursor, rnd, user_id, current)
    logger.info("Добавлены демо-данные: %s товаров, %s продаж, %s клиентов.", len(products), len(sales), clients['customers'])
    return True, {"products": len(products), "sales": len(sales), **clients}


@with_transaction
def seed_demo_customers(cursor, seed=None):
    """Демо-клиенты к уже добавленному демо-каталогу (для баз, где демо появилось раньше клиентов)."""
    cursor.execute("SELECT 1 FROM customers WHERE consent_source = %s LIMIT 1", (CUSTOMER_SOURCE,))
    if cursor.fetchone():
        raise BusinessError("Демо-клиенты уже добавлены.")
    cursor.execute("SELECT id FROM users WHERE is_active = 1 ORDER BY role = 'admin' DESC, id LIMIT 1")
    user = cursor.fetchone()
    if not user:
        raise BusinessError("Сначала добавьте администратора.")
    result = _seed_customers(cursor, random.Random(seed), user['id'], now())
    logger.info("Добавлены демо-клиенты: %s, покупок %s.", result['customers'], result['purchases'])
    return True, result


def _birthday(rnd, current, soon=False):
    if soon:          # у пары клиентов день рождения на этой неделе — видно на главной и в базе клиентов
        d = (current + timedelta(days=rnd.randint(0, 6))).date()
    else:
        d = (current - timedelta(days=rnd.randint(0, 364))).date()
    year = rnd.randint(1966, 2001)
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


def _seed_customers(cursor, rnd, user_id, current):
    """Клиенты с историей за год: постоянные, растущие, новые, разовые, «под угрозой» и уснувшие.
    Покупки за последние две недели — это уже добавленные демо-чеки без клиента, старые — новые чеки по демо-товарам
    (остаток не трогают: считается, что товар с тех пор довозили)."""
    cursor.execute("""SELECT id, article_number, store_id, product_type, metal_type, price, min_price FROM products
                      WHERE article_number LIKE %s""", (PREFIX + "%",))
    products = cursor.fetchall()
    if not products:
        raise BusinessError("Сначала добавьте демо-данные: клиентам нужны демо-товары.")
    for p in products:
        p['price'], p['min_price'] = int(p['price'] or 0), int(p['min_price'] or 0)
    stores = sorted({p['store_id'] for p in products})
    by_store = {st: [p for p in products if p['store_id'] == st] for st in stores}
    types = sorted({p['product_type'] for p in products})
    # демо-чеки без клиента и без возвратов: к ним «привязываются» недавние покупки
    cursor.execute("""SELECT DISTINCT r.id, r.store_id, r.created_at FROM receipts r JOIN sales s ON s.receipt_id = r.id
                      JOIN products p ON p.id = s.product_id
                      WHERE p.article_number LIKE %s AND r.customer_id IS NULL
                        AND NOT EXISTS (SELECT 1 FROM sales s2 JOIN returns rt ON rt.original_sale_id = s2.id
                                        WHERE s2.receipt_id = r.id)
                      ORDER BY r.created_at""", (PREFIX + "%",))
    free = cursor.fetchall()
    rnd.shuffle(free)

    def when(days_ago):
        t = (current - timedelta(days=days_ago)).replace(hour=rnd.randint(10, 19), minute=rnd.randint(0, 59),
                                                          second=rnd.randint(0, 59))
        return min(t, current)

    names = list(CUSTOMER_NAMES)
    rnd.shuffle(names)
    customers = purchases = attached = 0
    number = 0
    soon_left = 3
    for persona, count, (n_min, n_max), (last_min, last_max), first_range in PERSONAS:
        for _ in range(count):
            number += 1
            name = names.pop() if names else None
            male = bool(name and name.split()[0] in MALE_FIRST)
            n = rnd.randint(n_min, n_max)
            last_ago = rnd.randint(last_min, last_max)
            first_ago = max(last_ago, rnd.randint(*first_range)) if first_range and n > 1 else last_ago
            days = sorted({last_ago, first_ago} | {rnd.randint(last_ago, first_ago) for _ in range(max(0, n - 2))},
                          reverse=True)
            while len(days) < n:                       # совпали дни — добавляем соседние
                days.append(max(0, days[-1] - 1))
            moments = sorted(when(d) for d in days[:n])
            home = rnd.choice(stores)
            favorites = (["печатка", "цепь", "браслет"] if male else rnd.sample(types, k=min(2, len(types))))
            favorites = [t for t in favorites if t in types] or types
            birthday = None
            if name and rnd.random() < 0.8:
                soon = soon_left > 0 and persona in ("Постоянные", "Под угрозой")
                soon_left -= soon
                birthday = _birthday(rnd, current, soon)
            note = MARK + rnd.choice(NOTES) if rnd.random() < 0.3 else None
            cursor.execute("""INSERT INTO customers (phone, name, birthday, note, consent_at, consent_source, consent_by,
                                  marketing, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                           (f"{CUSTOMER_PHONE}{number:04d}", name, birthday, note, moments[0], CUSTOMER_SOURCE, user_id,
                            1 if rnd.random() < 0.6 else 0, moments[0], moments[0]))
            customer_id = cursor.lastrowid
            customers += 1
            for moment in moments:
                purchases += 1
                # покупка за последние две недели — это один из демо-чеков (ближайший по дате)
                if (current - moment).days < 14 and free:
                    receipt = min(free, key=lambda r: abs((r['created_at'] - moment).total_seconds()))
                    free.remove(receipt)
                    cursor.execute("UPDATE receipts SET customer_id = %s WHERE id = %s", (customer_id, receipt['id']))
                    attached += 1
                    continue
                store = home if rnd.random() < 0.85 else rnd.choice(stores)
                pool = by_store[store]
                liked = [p for p in pool if p['product_type'] in favorites] or pool
                lines = []
                for _ in range(rnd.choices([1, 2], weights=[80 if persona != "Постоянные" else 60, 20])[0]):
                    p = rnd.choice(liked if rnd.random() < 0.75 else pool)
                    price = p['price'] if rnd.random() < 0.7 else max(p['min_price'], int(round(p['price'] * 0.95, -2)))
                    lines.append((p, price))
                payment = rnd.choice(PAYMENTS)
                cursor.execute("""INSERT INTO receipts (store_id, user_id, created_at, total, items_count, payment_type,
                                      customer_id, public_token) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                               (store, user_id, moment, sum(pr for _, pr in lines), len(lines), payment, customer_id,
                                secrets.token_hex(16)))
                receipt_id = cursor.lastrowid
                for p, price in lines:
                    cursor.execute("""INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type, store_id,
                                          receipt_id, list_price) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                   (p['id'], user_id, moment, price, payment, store, receipt_id, p['price']))
    # товар должен поступить раньше первой продажи
    cursor.execute("""UPDATE products p JOIN (SELECT product_id, MIN(sale_date) AS first FROM sales GROUP BY product_id) s
                      ON s.product_id = p.id
                      SET p.arrival_date = LEAST(COALESCE(p.arrival_date, DATE(s.first)), DATE(s.first) - INTERVAL 3 DAY)
                      WHERE p.article_number LIKE %s""", (PREFIX + "%",))
    return {"customers": customers, "purchases": purchases, "attached": attached}


@with_transaction
def remove_demo(cursor):
    """Удаляет демо-товары и все операции с ними, демо-скупку и демо-инкассацию."""
    like = PREFIX + "%"
    cursor.execute("SELECT id FROM products WHERE article_number LIKE %s", (like,))
    ids = [r['id'] for r in cursor.fetchall()]
    removed = 0
    if ids:
        marks = ",".join(["%s"] * len(ids))
        cursor.execute(f"""DELETE r FROM returns r JOIN sales s ON s.id = r.original_sale_id
                           WHERE s.product_id IN ({marks})""", ids)
        removed += cursor.rowcount
        cursor.execute(f"SELECT DISTINCT receipt_id FROM sales WHERE product_id IN ({marks}) AND receipt_id IS NOT NULL", ids)
        receipts = [r['receipt_id'] for r in cursor.fetchall()]
        cursor.execute(f"DELETE FROM sales WHERE product_id IN ({marks})", ids)
        removed += cursor.rowcount
        if receipts:
            # чек удаляется, если в нём не осталось настоящих позиций; иначе пересчитывается
            rmarks = ",".join(["%s"] * len(receipts))
            cursor.execute(f"""DELETE r FROM receipts r LEFT JOIN sales s ON s.receipt_id = r.id
                               WHERE r.id IN ({rmarks}) AND s.id IS NULL""", receipts)
            cursor.execute(f"""UPDATE receipts r JOIN (SELECT receipt_id, SUM(sold_price) AS total, COUNT(*) AS n
                                FROM sales WHERE receipt_id IN ({rmarks}) GROUP BY receipt_id) x ON x.receipt_id = r.id
                               SET r.total = x.total, r.items_count = x.n""", receipts)
            cursor.execute(f"""DELETE rp FROM receipt_payments rp LEFT JOIN receipts r ON r.id = rp.receipt_id
                               WHERE rp.receipt_id IN ({rmarks}) AND r.id IS NULL""", receipts)
            cursor.execute(f"""UPDATE receipt_payments rp JOIN receipts r ON r.id = rp.receipt_id
                               SET rp.amount = r.total
                               WHERE rp.receipt_id IN ({rmarks}) AND r.payment_type != 'Смешанная'""", receipts)
    cursor.execute("DELETE FROM trade_ins WHERE new_product_article LIKE %s OR old_product_article LIKE %s", (like, like))
    removed += cursor.rowcount
    cursor.execute("DELETE FROM buybacks WHERE product_article LIKE %s OR description LIKE %s", (like, MARK + "%"))
    removed += cursor.rowcount
    cursor.execute("DELETE FROM cash_withdrawals WHERE comment LIKE %s", (MARK + "%",))
    removed += cursor.rowcount
    cursor.execute("DELETE c FROM product_codes c JOIN products p ON p.id = c.product_id WHERE p.article_number LIKE %s",
                   (like,))
    cursor.execute("DELETE FROM products WHERE article_number LIKE %s", (like,))
    products = cursor.rowcount
    # демо-клиенты: чеки, если сотрудники пробили им настоящие товары, остаются без привязки
    cursor.execute("""UPDATE receipts r JOIN customers c ON c.id = r.customer_id SET r.customer_id = NULL
                      WHERE c.consent_source = %s""", (CUSTOMER_SOURCE,))
    cursor.execute("DELETE FROM customers WHERE consent_source = %s", (CUSTOMER_SOURCE,))
    clients = cursor.rowcount
    logger.info("Удалены демо-данные: %s товаров, %s операций, %s клиентов.", products, removed, clients)
    return True, {"products": products, "operations": removed, "customers": clients}
