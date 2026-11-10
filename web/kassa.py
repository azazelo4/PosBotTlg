"""Касса для ПК в магазине: обычный браузер вместо Telegram.

Вход без паролей: страница показывает QR со ссылкой на бота (t.me/<бот>?start=k_<токен>),
сотрудник сканирует его телефоном и подтверждает вход в боте. Браузер, показавший QR,
опрашивает статус и получает сессию кассы. Токен хранится только в сессии этого браузера
и в виде хэша в БД, одноразовый, живёт 5 минут.

Операции — те же функции БД, что у бота, поэтому отчёты и остатки общие.
"""
import hashlib
import logging
import secrets
import time
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Request

import config
from buttons import PAYMENT_TYPES, ROLE_TITLES
from database import (get_user_by_id, get_stores, get_product_by_article, record_receipt, MAX_RECEIPT_ITEMS, get_receipt,
                      find_by_mark_code, marking_required, SELLABLE_CODE, CODE_STATUS_TITLES, get_sales_for_return,
                      get_sale_details_by_id, create_return_record, create_buyback_record, create_trade_in_record,
                      create_cash_withdrawal_record, get_period_operations, create_kassa_login, consume_kassa_login)
from schema import METAL_TYPES
from utils import mark_codes
from utils.common import now, today, day_bounds, parse_money, parse_float, money, normalize_phone, parse_birthday
from utils import receipt_text
from utils.labels import details_line
from utils.report_generator import build_summary

logger = logging.getLogger("posbot.kassa")

CASH_ROLES = ('admin', 'manager', 'cashier')
LOGIN_TTL = timedelta(minutes=5)
SHIFT_HOURS = 14          # вход на кассу действует не дольше смены
START_PREFIX = "k_"       # /start k_<токен>

router = APIRouter(prefix="/kassa/api")


def token_hash(token):
    return hashlib.sha256(("kassa:" + token).encode()).hexdigest()


@lru_cache(maxsize=4)
def _bot_username(token):
    from telebot import apihelper
    return apihelper.get_me(token).get('username')


def bot_username():
    """@username бота без @ (getMe к Telegram, кэш по токену) или None."""
    if not config.TOKEN:
        return None
    try:
        return _bot_username(config.TOKEN)
    except Exception as e:
        logger.warning("Не удалось узнать имя бота: %s", e)
        return None


def qr_svg(text):
    """QR-код в SVG (reportlab уже есть для ценников)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    widget = QrCodeWidget(text, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    size = 240
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), -x1, -y1])
    drawing.add(widget)
    out = StringIO()
    renderSVG.drawToFile(drawing, out)
    svg = out.getvalue()
    return svg[svg.index("<svg"):]


def client_info(request: Request):
    """Коротко о компьютере для сообщения в боте: «Windows · Chrome · 1.2.3.4»."""
    ua = request.headers.get("user-agent", "")
    system = next((name for key, name in (("Windows", "Windows"), ("Mac OS", "macOS"), ("Android", "Android"),
                                          ("iPhone", "iPhone"), ("Linux", "Linux")) if key in ua), "")
    browser = next((name for key, name in (("Edg/", "Edge"), ("YaBrowser", "Яндекс Браузер"), ("OPR/", "Opera"),
                                           ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
                    if key in ua), "")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    return " · ".join(p for p in (system, browser, ip) if p)


# ---------------------------------------------------------------- сессия кассы

def logout(request: Request):
    for key in ("kassa_user_id", "kassa_since", "kassa_store_id", "kassa_pending"):
        request.session.pop(key, None)


def kassa_user(request: Request):
    """Сотрудник, вошедший на кассу в этом браузере. Роль и активность проверяются при каждом запросе."""
    user_id = request.session.get("kassa_user_id")
    since = request.session.get("kassa_since") or 0
    if not user_id or time.time() - since > SHIFT_HOURS * 3600:
        logout(request)
        raise HTTPException(401, "Войдите на кассу: отсканируйте QR-код телефоном.")
    user = get_user_by_id(user_id)
    if not user or not user.get('is_active') or user.get('role') not in CASH_ROLES:
        logout(request)
        raise HTTPException(401, "Доступ к кассе закрыт. Обратитесь к администратору.")
    return user


def same_origin(request: Request):
    """Защита от подделки запросов: изменяющие запросы — только JSON со своей страницы."""
    if request.headers.get("x-kassa") != "1":
        raise HTTPException(400, "Обновите страницу кассы.")


def kassa_store_id(request: Request, user):
    """Магазин операций без товара (скупка стороннего изделия, инкассация, итоги)."""
    stores = get_stores() or []
    ids = {s['id'] for s in stores}
    chosen = request.session.get("kassa_store_id")
    if user.get('store_id'):
        return user['store_id']
    if chosen in ids:
        return chosen
    if len(stores) == 1:
        return stores[0]['id']
    return None


def need_store(request: Request, user):
    store_id = kassa_store_id(request, user)
    if not store_id:
        raise HTTPException(400, "Выберите магазин кассы вверху экрана.")
    return store_id


# ---------------------------------------------------------------- вход

@router.post("/login")
def start_login(request: Request, _=Depends(same_origin)):
    username = bot_username()
    if not username:
        raise HTTPException(503, "Бот ещё не настроен или Telegram недоступен. Попробуйте через минуту.")
    token = secrets.token_urlsafe(24)
    ok, _msg = create_kassa_login(token_hash(token), now() + LOGIN_TTL, client_info(request))
    if not ok:
        raise HTTPException(500, "Не удалось создать вход. Попробуйте ещё раз.")
    request.session["kassa_pending"] = token
    link = f"https://t.me/{username}?start={START_PREFIX}{token}"
    return {"link": link, "qr": qr_svg(link), "bot": "@" + username, "ttl": int(LOGIN_TTL.total_seconds())}


@router.get("/login")
def login_status(request: Request):
    """pending | approved (сессия выдана) | denied | expired."""
    token = request.session.get("kassa_pending")
    if not token:
        return {"status": "expired"}
    ok, result = consume_kassa_login(token_hash(token))
    if ok:
        user = get_user_by_id(result)
        if not user or not user.get('is_active') or user.get('role') not in CASH_ROLES:
            request.session.pop("kassa_pending", None)
            return {"status": "denied"}
        request.session.pop("kassa_pending", None)
        request.session["kassa_user_id"] = user['id']
        request.session["kassa_since"] = int(time.time())
        logger.info("Касса ПК: вход сотрудника %s (%s)", user['id'], client_info(request))
        return {"status": "approved"}
    if result in ("denied", "expired", "used"):
        request.session.pop("kassa_pending", None)
    return {"status": result}


@router.post("/logout")
def do_logout(request: Request, _=Depends(same_origin)):
    logout(request)
    return {"ok": True}


# ---------------------------------------------------------------- данные кассы

def product_json(p, code=None):
    data = {"id": p['id'], "article": p['article_number'], "model": p['model'], "type": p['product_type'],
            "details": details_line(p), "inserts": p.get('inserts'), "store": p['Store'],
            "price": int(p['price'] or 0), "min_price": int(p['min_price'] or 0), "quantity": p['quantity'],
            "marked": marking_required(p)}
    if code:
        data["code"] = code['code']
        data["code_short"] = mark_codes.short(code['code'])
    return data


def lookup(text, for_sale=False):
    """Артикул, «#id» или код маркировки → (товар, строка кода | None)."""
    text = str(text or "").strip()
    if mark_codes.looks_like_code(text):
        found = find_by_mark_code(text)
        if found:
            code = found['code']
            if for_sale and code['status'] not in SELLABLE_CODE:
                raise HTTPException(400, f"Код {mark_codes.short(text)} уже {CODE_STATUS_TITLES[code['status']]}.")
            return found['product'], code
        p = get_product_by_article(text)
        if p:
            return p, None
        raise HTTPException(404, f"Код {mark_codes.short(text)} не найден: его не приняли на склад (раздел «Маркировка» в админке).")
    return get_product_by_article(text), None


@router.get("/me")
def me(request: Request, user=Depends(kassa_user)):
    stores = get_stores() or []
    return {
        "id": user['id'], "name": " ".join(p for p in (user.get('name'), user.get('surename')) if p),
        "role": ROLE_TITLES.get(user['role'], user['role']),
        "fixed_store": bool(user.get('store_id')), "store_id": kassa_store_id(request, user),
        "stores": [{"id": s['id'], "name": s['name']} for s in stores],
        "payment_types": list(PAYMENT_TYPES), "metal_types": list(METAL_TYPES),
        "org": config.ORG_NAME or "",
    }


@router.post("/store")
async def choose_store(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    body = await request.json()
    store_id = body.get("store_id")
    if user.get('store_id'):
        raise HTTPException(400, "Сотрудник закреплён за магазином — сменить его может администратор.")
    if store_id not in {s['id'] for s in get_stores() or []}:
        raise HTTPException(400, "Нет такого магазина.")
    request.session["kassa_store_id"] = store_id
    return {"store_id": store_id}


@router.get("/product")
def product(article: str, sale: int = 0, user=Depends(kassa_user)):
    """sale=1 — товар для продажи: код маркировки должен быть доступен для продажи."""
    p, code = lookup(article, for_sale=bool(sale))
    if not p:
        raise HTTPException(404, f"Артикул {article.strip()} не найден.")
    return product_json(p, code)


# ---------------------------------------------------------------- проверки ввода

def _money(value, what, allow_zero=False):
    amount = parse_money(str(value if value is not None else ""))
    if amount is None or amount < 0 or (amount == 0 and not allow_zero):
        raise HTTPException(400, f"Введите {what} числом больше нуля.")
    if amount > Decimal("1000000000"):
        raise HTTPException(400, f"Слишком большая {what}.")
    return amount.quantize(Decimal('1'))


def _payment(value, what="способ оплаты"):
    if value not in PAYMENT_TYPES:
        raise HTTPException(400, f"Выберите {what}.")
    return value


def _for_sale(article):
    p, _ = lookup(article, for_sale=True)
    if not p:
        raise HTTPException(404, "Товар не найден.")
    if (p['quantity'] or 0) <= 0:
        raise HTTPException(400, f"Товара {p['article_number']} нет в наличии.")
    return p


def _check_min(p, price):
    if price < (p['min_price'] or 0):
        raise HTTPException(400, f"Сумма ниже минимальной цены {money(p['min_price'])}.")


def _item(data):
    """Изделие клиента для скупки и trade-in: наше по артикулу или стороннее (металл, вес, описание)."""
    data = data or {}
    article = str(data.get("article") or "").strip()
    if article:
        p, _ = lookup(article)
        if not p:
            raise HTTPException(404, f"Артикул {article} не найден. Если изделие не наше — выберите «Без артикула».")
        return {"article": p['article_number'], "description": p.get('model'), "metal_type": p.get('metal_type'),
                "weight": p.get('weight')}
    metal = data.get("metal")
    if metal not in METAL_TYPES:
        raise HTTPException(400, "Выберите металл изделия.")
    weight = parse_float(str(data.get("weight") or ""))
    if weight is None or weight <= 0 or weight > 10000:
        raise HTTPException(400, "Вес изделия — положительное число граммов, например 3.45.")
    description = str(data.get("description") or "").strip()[:500] or None
    return {"article": None, "description": description, "metal_type": metal, "weight": round(weight, 3)}


def _result(ok, result, failed):
    """Результат функции БД (успех, id | причина) → id или ошибка «<failed>: <причина>»."""
    if not ok:
        raise HTTPException(400, f"{failed}: {result}")
    return result


# ---------------------------------------------------------------- операции

@router.post("/sale")
async def sale(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    """Чек: {"items": [{"article", "price"}, ...], "payment"}; одиночная продажа {"article", "price"} тоже принимается."""
    body = await request.json()
    raw = body.get("items")
    if raw is None:
        raw = [{"article": body.get("article"), "price": body.get("price")}]
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Чек пуст — добавьте товар.")
    if len(raw) > MAX_RECEIPT_ITEMS:
        raise HTTPException(400, f"В одном чеке — не больше {MAX_RECEIPT_ITEMS} позиций.")
    items, names = [], []
    for row in raw:
        row = row if isinstance(row, dict) else {}
        p = _for_sale(row.get("article"))
        price = _money(row.get("price"), f"сумму для {p['article_number']}")
        _check_min(p, price)
        code = str(row.get("code") or "").strip() or None
        items.append((p['id'], price, code))
        names.append(p['model'] or p['article_number'])
    payment = _payment(body.get("payment"))
    customer = _customer(body.get("customer"))
    result = _result(*record_receipt(items, user['id'], payment, kassa_store_id(request, user), customer),
                     "Продажа не оформлена")
    logger.info("Касса ПК: чек #%s, %s поз. на %s (%s)", result['receipt_id'], len(items), result['total'], user['id'])
    what = names[0] if len(names) == 1 else f"{len(names)} поз."
    return {"id": result['receipt_id'], "sale_ids": result['sale_ids'],
            "text": f"Чек №{result['receipt_id']}: {what}, {money(result['total'])}, {payment}",
            "receipt": receipt_delivery(result['receipt_id'], customer)}


def _customer(raw):
    """Телефон клиента из чека — только с отмеченным согласием на хранение номера."""
    raw = raw if isinstance(raw, dict) else {}
    phone = str(raw.get("phone") or "").strip()
    if not phone:
        return None
    if not normalize_phone(phone):
        raise HTTPException(400, "Телефон клиента: введите номер вида +7 701 123 45 67 или уберите его.")
    if not raw.get("consent"):
        raise HTTPException(400, "Спросите у клиента согласие на хранение номера и отметьте его — или уберите номер.")
    birthday = str(raw.get("birthday") or "").strip()
    born = parse_birthday(birthday) if birthday else None
    if birthday and not born:
        raise HTTPException(400, "День рождения клиента: ДД.ММ или ДД.ММ.ГГГГ, например 12.05 — или сотрите его.")
    return {"phone": phone, "name": str(raw.get("name") or "").strip()[:100] or None, "birthday": born}


def receipt_delivery(receipt_id, customer=None):
    """Как отдать чек клиенту: QR на бота, WhatsApp, страница для печати."""
    receipt = get_receipt(receipt_id)
    if not receipt:
        return None
    tg = receipt_text.bot_link(bot_username(), receipt)
    phone = (customer or {}).get("phone")
    return {"id": receipt['id'], "url": f"/r/{receipt['public_token']}", "public_url": receipt_text.public_url(receipt),
            "telegram": tg, "qr": qr_svg(tg) if tg else None,
            "whatsapp": receipt_text.whatsapp_url(normalize_phone(phone), receipt) if phone else None,
            "telegram_auto": bool(receipt.get('customer_chat'))}


@router.get("/sales-for-return")
def sales_for_return(article: str, user=Depends(kassa_user)):
    sales = get_sales_for_return(article.strip())
    if sales is None:
        raise HTTPException(500, "Ошибка базы данных. Попробуйте ещё раз.")
    return [{"sale_id": s['sale_id'], "date": s['sale_date'].strftime('%d.%m.%Y %H:%M'), "price": int(s['sold_price']),
             "payment": s['sale_payment_type'], "model": s['product_model'], "article": s['article_number'],
             "returned": bool(s['return_id'])} for s in sales]


@router.post("/return")
async def do_return(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    body = await request.json()
    try:
        sale_id = int(body.get("sale_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Выберите продажу.")
    s = get_sale_details_by_id(sale_id)
    if not s or s['return_id']:
        raise HTTPException(400, "Эта продажа недоступна для возврата.")
    price = _money(body.get("price"), "сумму возврата")
    if price > s['sold_price']:
        raise HTTPException(400, f"Сумма возврата не может быть больше суммы продажи {money(s['sold_price'])}.")
    payment = _payment(body.get("payment"), "способ выплаты")
    return_id = _result(*create_return_record(sale_id, s['article_number'], price, payment, user['id'],
                                              kassa_store_id(request, user)), "Возврат не оформлен")
    logger.info("Касса ПК: возврат #%s по продаже #%s (%s)", return_id, sale_id, user['id'])
    return {"id": return_id, "text": f"Возврат №{return_id}: {money(price)}, {payment}. Товар вернулся на склад."}


@router.post("/buyback")
async def buyback(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    body = await request.json()
    item = _item(body.get("item"))
    price = _money(body.get("price"), "сумму скупки")
    payment = _payment(body.get("payment"), "способ выплаты")
    # своё изделие относится к магазину товара (это сделает БД), для чужого нужен магазин кассы
    store_id = kassa_store_id(request, user) if item['article'] else need_store(request, user)
    buyback_id = _result(*create_buyback_record(item['article'], price, payment, user['id'], store_id,
                                                description=item['description'], metal_type=item['metal_type'],
                                                weight=item['weight']), "Скупка не оформлена")
    logger.info("Касса ПК: скупка #%s на %s (%s)", buyback_id, price, user['id'])
    return {"id": buyback_id, "text": f"Скупка №{buyback_id}: выплата {money(price)}, {payment}."}


@router.post("/tradein")
async def tradein(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    body = await request.json()
    item = _item(body.get("item"))
    old_value = _money(body.get("old_value"), "оценку сдаваемого изделия")
    p = _for_sale(body.get("new_article"))
    if p['article_number'] == item['article']:
        raise HTTPException(400, "Новый товар не может совпадать со сдаваемым.")
    new_price = _money(body.get("new_price"), "цену нового товара")
    _check_min(p, new_price)
    diff = new_price - old_value
    payment = _payment(body.get("payment")) if diff else None
    tradein_id = _result(*create_trade_in_record(
        new_product_article=p['article_number'], new_sale_price=new_price, old_item_value=old_value,
        price_difference=diff, payment_type_difference=payment, user_id=user['id'],
        store_id=kassa_store_id(request, user), old_product_article=item['article'],
        old_item_description=item['description'], old_metal_type=item['metal_type'], old_weight=item['weight'],
        new_mark_code=str(body.get("new_code") or "").strip() or None),
        "Trade-In не оформлен")
    logger.info("Касса ПК: trade-in #%s, доплата %s (%s)", tradein_id, diff, user['id'])
    if diff > 0:
        tail = f"доплата клиента {money(diff)}, {payment}"
    elif diff < 0:
        tail = f"выплата клиенту {money(-diff)}, {payment}"
    else:
        tail = "без доплаты"
    return {"id": tradein_id, "text": f"Trade-In №{tradein_id}: {tail}."}


@router.post("/withdrawal")
async def withdrawal(request: Request, user=Depends(kassa_user), _=Depends(same_origin)):
    body = await request.json()
    amount = _money(body.get("amount"), "сумму инкассации")
    comment = str(body.get("comment") or "").strip()[:500] or None
    store_id = need_store(request, user)
    withdrawal_id = _result(*create_cash_withdrawal_record(amount, comment, user['id'], store_id), "Инкассация не оформлена")
    logger.info("Касса ПК: инкассация #%s на %s (%s)", withdrawal_id, amount, user['id'])
    return {"id": withdrawal_id, "text": f"Инкассация №{withdrawal_id}: {money(amount)}."}


# ---------------------------------------------------------------- итоги смены

def _journal(ops):
    """Операции дня одним списком, новые сверху."""
    rows = []
    receipts = {}
    for s in ops['sales']:   # позиции одного чека — одной строкой
        key = s.get('receipt_id') or -s['id']
        receipts.setdefault(key, []).append(s)
    for key, items in receipts.items():
        s = items[0]
        what = f"{s['model'] or ''} · {s['article_number']}" if len(items) == 1 else \
            f"{len(items)} поз.: " + ", ".join(i['model'] or i['article_number'] for i in items)
        if key > 0:
            what = f"чек №{key} · {what}"
        rows.append((s['sale_date'], "Продажа", what, sum(int(i['sold_price']) for i in items), s['payment_type'], s['user_name']))
    for r in ops['returns']:
        rows.append((r['return_date'], "Возврат", f"{r['model'] or ''} · {r['product_article']} (продажа №{r['original_sale_id']})",
                     -int(r['return_price']), r['payment_type'], r['user_name']))
    for b in ops['buybacks']:
        what = b['product_article'] or f"{b['metal_type']}, {b['weight']} г"
        rows.append((b['buyback_date'], "Скупка", f"{b['description'] or ''} · {what}".strip(" ·"),
                     -int(b['buyback_price']), b['payment_type'], b['user_name']))
    for t in ops['trade_ins']:
        rows.append((t['trade_in_date'], "Trade-In", f"{t['new_model'] or ''} · {t['new_product_article']}",
                     int(t['price_difference']), t['payment_type_difference'] or "—", t['user_name']))
    for w in ops['withdrawals']:
        rows.append((w['withdrawal_date'], "Инкассация", w['comment'] or "", -int(w['amount']), "Наличные", w['user_name']))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [{"time": r[0].strftime('%H:%M'), "kind": r[1], "what": r[2], "amount": r[3], "payment": r[4],
             "user": r[5] or ""} for r in rows]


@router.get("/shift")
def shift(request: Request, user=Depends(kassa_user)):
    store_id = kassa_store_id(request, user)
    d = today()
    ops = get_period_operations(*day_bounds(d, d), store_id=store_id)
    if ops is None:
        raise HTTPException(500, "Ошибка базы данных. Попробуйте ещё раз.")
    summary = build_summary(ops)
    store = next((s['name'] for s in get_stores(active_only=False) or [] if s['id'] == store_id), None)
    return {
        "date": d.strftime('%d.%m.%Y'), "store": store or "все магазины",
        "sales_count": summary["sales_count"], "receipts_count": summary["receipts_count"], "sales_total": int(summary['sales_total']),
        "returns_count": summary['returns_count'], "returns_total": int(summary['returns_total']),
        "buybacks_count": summary['buybacks_count'], "buybacks_total": int(summary['buybacks_total']),
        "trade_ins_count": summary['trade_ins_count'],
        "withdrawals_total": int(summary['withdrawals_total']),
        "flow": {k: int(v) for k, v in summary['flow'].items()},
        "cash_balance": int(summary['cash_balance']),
        "journal": _journal(ops),
    }
