"""Веб-админка и Telegram Mini App поверх той же БД, что и бот.

Запуск: uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
"""
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import jinja2
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from services import analytics
from core import config
from services import demo_data
from services import customers
from services import fiscal
from services import inventory
from services import marking
from services import photos
from core import logsafe
from core import settings_store
from handlers.buttons import PAYMENT_TYPES, ROLE_TITLES
from utils import receipt_text
from core.database import (own_store, get_receipt, get_job_run, marking_required, ensure_admin, get_connection, get_stores, get_store_counts, create_store, update_store, set_user_store, init_pool, get_user_by_id, get_user_by_chat_id, search_products, get_product_by_id,
                      get_product_by_article, get_products_by_articles, get_products_for_labels, add_product_to_db,
                      update_product, get_stock_report_data, get_low_stock_products, get_period_operations,
                      record_sale, get_today_sales_by_user, get_all_users, add_user_to_db, change_user_role_function,
                      deactivate_user_in_db, update_user_phone, create_cash_deposit_record, recent_cash_operations, foreign_store_error)
from core.schema import METAL_TYPES, PRODUCT_TYPES, ensure_schema
from utils.catalog_import import build_plan, apply_plan, attach_photos, FIELD_TITLES
from utils.customer_import import parse_customers, birthday_text, FIELD_TITLES as CUSTOMER_FIELD_TITLES
from utils.photo_archive import PhotoArchive, ArchiveError
from utils import mark_codes
from utils.common import (money, today, now, parse_money, parse_float, parse_int, parse_date, normalize_phone,
                          day_bounds, parse_birthday, format_birthday)
from utils.labels import generate_labels_pdf, details_line
from utils.report_generator import build_summary, build_excel, store_breakdown
from utils.analytics_excel import build_analytics_excel
from utils.stock_report import build_stock_excel, stock_totals
from web import settings_form, kassa, manual
from web.security import (session_secret, redeem_login_token, validate_init_data, new_csrf, ADMIN_ROLES, CASH_ROLES)

logsafe.install()          # токен бота и текст сообщений не попадают в логи (аудит, Н-2)
logger = logging.getLogger("posbot.web")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters['money'] = money
templates.env.filters['birthday'] = format_birthday


def _num(value):
    """Целое для полей формы: '' для пустых/неопределённых значений."""
    if isinstance(value, jinja2.Undefined) or value in (None, ''):
        return ''
    try:
        return int(value)
    except (TypeError, ValueError):
        return ''


templates.env.filters['num'] = _num
templates.env.filters['ddmmyyyy'] = lambda d: d.strftime('%d.%m.%Y') if d else ''
templates.env.filters['ddmmhhmm'] = lambda d: d.strftime('%d.%m %H:%M') if d else ''
templates.env.globals['ROLE_TITLES'] = ROLE_TITLES
templates.env.globals['TIMEZONES'] = config.TIMEZONES
templates.env.globals['VERSION'] = config.VERSION
templates.env.globals['marking_enabled'] = lambda: bool(config.MARKING_ENABLED)
templates.env.globals['fiscal_enabled'] = lambda: bool(config.FISCAL_ENABLED)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def prepare():
    """Миграции и настройки при старте веба (бот может ещё ждать токен или не стартовать вовсе)."""
    init_pool()
    connection = get_connection()
    try:
        ensure_schema(connection, config.DEFAULT_STORES)
    finally:
        connection.close()
    settings_store.reload()
    settings_store.log_setup_hint()


@asynccontextmanager
async def lifespan(_app):
    await run_in_threadpool(prepare)
    yield


class DynamicSessionMiddleware:
    """Cookie сессии с флагом Secure, если запрос пришёл по HTTPS (через Caddy).
    По HTTP (SSH-туннель на 127.0.0.1:8000 во время настройки) — без него, иначе браузер её не вернёт."""

    def __init__(self, app):
        options = dict(secret_key=session_secret(), session_cookie="posbot_session",
                       max_age=config.WEB_SESSION_HOURS * 3600, same_site="lax")
        self.secure = SessionMiddleware(app, https_only=True, **options)
        self.plain = SessionMiddleware(app, https_only=False, **options)

    async def __call__(self, scope, receive, send):
        handler = self.secure if scope.get("scheme") in ("https", "wss") else self.plain
        await handler(scope, receive, send)


app = FastAPI(title="POS-бот", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(DynamicSessionMiddleware)


@app.middleware("http")
async def fresh_settings(request: Request, call_next):
    # Настройки меняются из бота/другого процесса редко: перечитываем не чаще раза в 5 секунд
    await run_in_threadpool(settings_store.reload_if_stale)
    return await call_next(request)

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

_import_plans = {}   # id -> (user_id, created, plan); один процесс uvicorn
IMPORT_TTL = 1800


class NeedLogin(Exception):
    pass


@app.exception_handler(NeedLogin)
def _need_login(request: Request, exc: NeedLogin):
    return templates.TemplateResponse(request, "login.html", {"user": None}, status_code=401)


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/kassa/api/")):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request, "error.html", {"user": None, "message": exc.detail},
                                      status_code=exc.status_code)


# ---------------------------------------------------------------- сессии

def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise NeedLogin()
    user = get_user_by_id(user_id)
    if not user or not user.get('is_active') or user.get('role') not in ADMIN_ROLES:
        request.session.clear()
        raise NeedLogin()
    return user


def admin_only(user=Depends(current_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, "Раздел доступен только администратору.")
    return user


def csrf_token(request: Request):
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = new_csrf()
    return token


def check_csrf(request: Request, token: str):
    if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(400, "Форма устарела. Обновите страницу и повторите.")


def flash(request: Request, text, kind="ok"):
    request.session.setdefault("flash", []).append([kind, text])


def render(request: Request, template, user, **ctx):
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, template, {"user": user, "csrf": csrf_token(request),
                                                          "messages": messages, **ctx})


@app.get("/login")
def login(request: Request, t: str = ""):
    if not settings_store.is_configured():
        return RedirectResponse("/setup", status_code=303)
    ok, result = redeem_login_token(t)
    if not ok:
        return templates.TemplateResponse(request, "login.html", {"user": None, "error": result}, status_code=401)
    user = get_user_by_id(result)
    if not user or not user.get('is_active') or user.get('role') not in ADMIN_ROLES:
        return templates.TemplateResponse(request, "login.html",
                                          {"user": None, "error": "Админка доступна менеджеру и администратору."},
                                          status_code=403)
    request.session.clear()
    request.session["user_id"] = user['id']
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- инструкции (web/manual.py)

def _optional_user(request: Request):
    """Вошедший в админку пользователь (для меню) или None — справка открыта всем."""
    user_id = request.session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    return user if user and user.get('is_active') and user.get('role') in ADMIN_ROLES else None


@app.get("/help", response_class=HTMLResponse)
def help_index(request: Request):
    return templates.TemplateResponse(request, "help.html", {"user": _optional_user(request), "slug": None,
                                                             "title": "Инструкции", "manuals": manual.MANUALS})


@app.get("/help/{slug}", response_class=HTMLResponse)
def help_page(request: Request, slug: str):
    page = manual.render(slug)
    if page is None:
        raise HTTPException(404, "Такой инструкции нет.")
    title, html = page
    return templates.TemplateResponse(request, "help.html", {"user": _optional_user(request), "slug": slug,
                                                             "title": title, "html": html, "manuals": manual.MANUALS})


@app.get("/health")
def health():
    """Состояние для установщика, `posbot status` и мониторинга. telegram_seconds_ago — когда бот последний раз
    достучался до Telegram (None — ещё ни разу или БД недоступна)."""
    seen = get_job_run("telegram")
    ago = int((now() - seen['last_run_at']).total_seconds()) if seen and seen.get('last_run_at') else None
    return {"ok": True, "configured": settings_store.is_configured(), "version": config.VERSION,
            "mode": config.INSTALL_MODE, "web_url": config.WEB_URL or "", "telegram_seconds_ago": ago}


# ---------------------------------------------------------------- дашборд

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not settings_store.is_configured():
        return RedirectResponse("/setup", status_code=303)
    return _dashboard(request, current_user(request))


def _dashboard(request: Request, user):
    d = today()
    periods = {}
    breakdown = []
    for key, start in (("today", d), ("month", d.replace(day=1))):
        ops = get_period_operations(*day_bounds(start, d))
        periods[key] = build_summary(ops) if ops is not None else None
        if key == "today" and ops is not None:
            breakdown = store_breakdown(ops)
    low = get_low_stock_products(config.LOW_STOCK_THRESHOLD) or []
    items = get_stock_report_data() or []
    return render(request, "dashboard.html", user, periods=periods, low_stock=low[:10], low_count=len(low), breakdown=breakdown,
                  demo=demo_data.has_demo(), birthdays=customers.upcoming_birthdays(7) or [],
                  totals=stock_totals(items), today=d, payment_types=PAYMENT_TYPES)


# ---------------------------------------------------------------- товары

PAGE_SIZE = 50


@app.get("/products", response_class=HTMLResponse)
def products(request: Request, q: str = "", store: str = "", stock: str = "", page: int = 1, user=Depends(current_user)):
    in_stock = {"yes": True, "no": False}.get(stock)
    page = max(page, 1)
    store_id = parse_int(store) if store else None
    rows, total = search_products(q.strip() or None, store_id, in_stock, PAGE_SIZE, (page - 1) * PAGE_SIZE) or ([], 0)
    thumbs = {pid: photos.signed_url(ph) for pid, ph in (photos.main_photos([r['id'] for r in rows]) or {}).items()}
    return render(request, "products.html", user, rows=rows, total=total, q=q, store=store_id, stock=stock, page=page,
                  thumbs=thumbs,
                  pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1), stores=get_stores() or [], details=details_line)


def _product_form_data(form):
    """Разбор формы товара → (data, errors)."""
    errors, data = [], {}

    def text(name, limit, required=False):
        v = (form.get(name) or "").strip()
        if required and not v:
            errors.append(f"Поле «{FIELD_TITLES.get(name, name)}» обязательно.")
        return v[:limit] or None

    data['article_number'] = text('article_number', 64, True)
    data['model'] = text('model', 255, True)
    store_id = parse_int(form.get('store_id') or '')
    if store_id not in {st['id'] for st in get_stores() or []}:
        errors.append("Выберите магазин.")
    data['store_id'] = store_id
    for key, options in (('product_type', PRODUCT_TYPES), ('metal_type', METAL_TYPES)):
        v = form.get(key)
        if v not in options:
            errors.append(f"Выберите «{FIELD_TITLES[key]}».")
        data[key] = v
    data['fineness'] = text('fineness', 16)
    data['size'] = text('size', 32)
    data['inserts'] = text('inserts', 255)
    data['manufacturer'] = text('manufacturer', 255)
    data['commentaries'] = text('commentaries', 500)
    data['barcode'] = re.sub(r"\s+", "", form.get('barcode') or "")[:64] or None
    weight = parse_float(form.get('weight') or '0')
    if weight is None or weight < 0:
        errors.append("Вес — неотрицательное число.")
    data['weight'] = weight or 0
    for key, required in (('price', True), ('min_price', True), ('price_per_gram', False)):
        raw = (form.get(key) or '').strip()
        v = parse_money(raw) if raw else (None if required else Decimal(0))
        if v is None or v < 0:
            errors.append(f"«{FIELD_TITLES[key]}» — число.")
        data[key] = v.quantize(Decimal('1')) if v is not None else None
    raw_cost = (form.get('cost_price') or '').strip()
    cost = parse_money(raw_cost) if raw_cost else None
    if raw_cost and (cost is None or cost < 0):
        errors.append("«Себестоимость» — число.")
    data['cost_price'] = cost.quantize(Decimal('1')) if cost is not None and cost >= 0 else None
    if data.get('price') is not None and data.get('min_price') is not None and data['min_price'] > data['price']:
        errors.append("Минимальная цена не может быть выше цены.")
    qty = parse_int(form.get('quantity') or '0')
    if qty is None or qty < 0:
        errors.append("Остаток — целое число от 0.")
    data['quantity'] = qty or 0
    data['track_stock'] = 1 if form.get('track_stock') else 0
    data['is_marked'] = 1 if form.get('is_marked') else 0
    arrival = parse_date(form.get('arrival_date') or '')
    if form.get('arrival_date') and not arrival:
        errors.append("Дата поступления: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.")
    data['arrival_date'] = arrival or today()
    return data, errors


def _form_ctx(product=None):
    product = product or {}
    gallery = [{**ph, "thumb": photos.signed_url(ph), "full": photos.signed_url(ph, thumb=False)}
               for ph in (photos.for_product(product['id']) or [] if product.get('id') else [])]
    return {"product": product, "stores": get_stores() or [], "types": PRODUCT_TYPES, "metals": METAL_TYPES,
            "photos": gallery, "max_photos": photos.MAX_PER_PRODUCT}


async def _save_photos(request, form, product_id, user):
    """Фото из поля формы «photos» (необязательно). Ошибки — сообщением, товар при этом сохранён."""
    added = 0
    for upload in form.getlist("photos"):
        if not hasattr(upload, "read") or not upload.filename:
            continue
        data = await upload.read(photos.MAX_BYTES + 1)
        ok, result = await run_in_threadpool(photos.add, product_id, data, user['id'])
        if ok:
            added += 1
        else:
            flash(request, f"Фото «{upload.filename}»: {result}", "error")
    if added:
        flash(request, f"Добавлено фото: {added}.")


@app.get("/products/new", response_class=HTMLResponse)
def product_new(request: Request, user=Depends(current_user)):
    return render(request, "product_form.html", user, **_form_ctx({'track_stock': 1, 'quantity': 1,
                                                                     'arrival_date': today()}))


@app.post("/products/new")
async def product_create(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    data, errors = _product_form_data(form)
    if not errors:
        ok, msg = add_product_to_db(data)
        if ok:
            flash(request, msg)
            created = get_product_by_article(data['article_number'])
            await _save_photos(request, form, created['id'], user)
            return RedirectResponse(f"/products/{created['id']}", status_code=303)
        errors.append(msg)
    return render(request, "product_form.html", user, errors=errors, **_form_ctx(data))


@app.get("/products/{product_id}", response_class=HTMLResponse)
def product_edit(request: Request, product_id: int, user=Depends(current_user)):
    product = get_product_by_id(product_id)
    if not product:
        raise HTTPException(404, "Товар не найден.")
    return render(request, "product_form.html", user, **_form_ctx(product))


@app.post("/products/{product_id}")
async def product_save(request: Request, product_id: int, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    data, errors = _product_form_data(form)
    if not errors:
        ok, msg = update_product(product_id, data)
        if ok:
            flash(request, msg)
            await _save_photos(request, form, product_id, user)
            return RedirectResponse(f"/products/{product_id}", status_code=303)
        errors.append(msg)
    data['id'] = product_id
    return render(request, "product_form.html", user, errors=errors, **_form_ctx(data))


@app.post("/products/{product_id}/photos/{photo_id}")
def product_photo_action(request: Request, product_id: int, photo_id: int, action: str = Form(...), csrf: str = Form(""),
                         user=Depends(current_user)):
    check_csrf(request, csrf)
    photo = photos.get(photo_id)
    if not photo or photo['product_id'] != product_id:
        raise HTTPException(404, "Фото не найдено.")
    if action == "delete":
        ok, _ = photos.delete(photo_id)
        flash(request, "Фото удалено." if ok else "Не удалось удалить фото.", "ok" if ok else "error")
    elif action == "main":
        ok, _ = photos.make_main(photo_id)
        flash(request, "Главное фото выбрано." if ok else "Не удалось.", "ok" if ok else "error")
    return RedirectResponse(f"/products/{product_id}#photos", status_code=303)


@app.get("/photos/{photo_id}/{size}")
def photo_file(photo_id: int, size: str, e: int = 0, s: str = ""):
    """Фото товара по ссылке с подписью (signed_url): админка, касса на ПК и Mini App."""
    if not photos.check_signature(photo_id, size, e, s):
        raise HTTPException(403, "Ссылка на фото устарела — обновите страницу.")
    photo = photos.get(photo_id)
    path = photos.file_path(photo['product_id'], photo['file'], size == "t") if photo else None
    if not path or not path.is_file():
        raise HTTPException(404, "Фото не найдено.")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=43200", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- маркировка

def _marking_page(request, user, **ctx):
    stores = get_stores() or []
    store = ctx.pop("store", None)
    return render(request, "marking.html", user, stores=stores, store=store,
                  products=marking.marked_products(store) or [], **ctx)


# ---------------------------------------------------------------- электронный чек и клиенты

@app.get("/r/{token}", response_class=HTMLResponse)
def public_receipt(request: Request, token: str):
    """Электронный чек для покупателя: без входа, по случайному токену из ссылки."""
    receipt = get_receipt(token=token) if len(token) == 32 and token.isalnum() else None
    if not receipt:
        return templates.TemplateResponse(request, "receipt_public.html", {"receipt": None, "user": None},
                                          status_code=404)
    return templates.TemplateResponse(request, "receipt_public.html", {
        "receipt": receipt, "user": None, "org": config.ORG_NAME, "bin": config.ORG_BIN, "footer": config.RECEIPT_FOOTER,
        "details": receipt_text.item_details}, headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


@app.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, q: str = "", user=Depends(current_user)):
    return render(request, "customers.html", user, rows=customers.search(q) or [], q=q,
                  birthdays=customers.upcoming_birthdays() or [] if not q else [])


# Добавление вручную и импорт — объявлены до /customers/{customer_id}, иначе «add» разбирался бы как id
_customer_plans = {}   # id -> (user_id, created, columns, rows, counts, filename)


@app.get("/customers/add", response_class=HTMLResponse)
def customer_add_page(request: Request, user=Depends(current_user)):
    return render(request, "customer_add.html", user, form={})


@app.post("/customers/add", response_class=HTMLResponse)
async def customer_add(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    values = {k: str(form.get(k) or "").strip() for k in ("phone", "name", "birthday", "note")}
    values["marketing"] = bool(form.get("marketing"))
    error = None
    born = parse_birthday(values["birthday"]) if values["birthday"] else None
    if not form.get("consent"):
        error = "Отметьте, что клиент согласился на хранение номера: без согласия добавлять нельзя."
    elif values["birthday"] and not born:
        error = "День рождения — ДД.ММ или ДД.ММ.ГГГГ, например 12.05 или 12.05.1995."
    if not error:
        ok, result = customers.create(values["phone"], values["name"], born, values["note"], values["marketing"], user['id'])
        if ok:
            flash(request, "Клиент добавлен.")
            return RedirectResponse(f"/customers/{result}", status_code=303)
        error = result
        existing = customers.by_phone(values["phone"])
        if existing:
            flash(request, result + " Открыта его карточка.", "error")
            return RedirectResponse(f"/customers/{existing['id']}", status_code=303)
    return render(request, "customer_add.html", user, form=values, error=error)


@app.post("/customers/import", response_class=HTMLResponse)
async def customer_import_upload(request: Request, file: UploadFile = File(...), csrf: str = Form(""),
                                 user=Depends(current_user)):
    check_csrf(request, csrf)
    data = await file.read(IMPORT_MAX + 1)
    try:
        if len(data) > IMPORT_MAX:
            raise ValueError("Файл больше 10 МБ.")
        columns, rows = await run_in_threadpool(parse_customers, data, file.filename or "")
        if not rows:
            raise ValueError("В файле нет строк с клиентами.")
    except ValueError as e:
        return render(request, "customer_add.html", user, form={}, import_error=str(e))
    counts = customers.import_preview(rows)
    if counts is None:
        raise HTTPException(500, "Ошибка базы данных.")
    cutoff = time.time() - IMPORT_TTL
    for key in [k for k, v in _customer_plans.items() if v[1] < cutoff]:
        _customer_plans.pop(key, None)
    plan_id = secrets.token_urlsafe(12)
    _customer_plans[plan_id] = (user['id'], time.time(), columns, rows, counts, file.filename)
    problems = [r for r in rows if r['status'] in ('error', 'dup') or r.get('warning')]
    return render(request, "customer_import.html", user, plan_id=plan_id, columns=columns, counts=counts,
                  sample=[r for r in rows if r['status'] in ('new', 'update')][:30], problems=problems[:100],
                  problems_total=len(problems), filename=file.filename, titles=CUSTOMER_FIELD_TITLES,
                  bday=birthday_text)


@app.post("/customers/import/{plan_id}")
async def customer_import_apply(request: Request, plan_id: str, csrf: str = Form(""), consent: str = Form(""),
                                user=Depends(current_user)):
    check_csrf(request, csrf)
    entry = _customer_plans.get(plan_id)
    if not entry or entry[0] != user['id']:
        flash(request, "Предпросмотр устарел, загрузите файл ещё раз.", "error")
        return RedirectResponse("/customers/add", status_code=303)
    if not consent:
        flash(request, "Подтвердите, что клиенты из файла дали согласие на хранение номера.", "error")
        return RedirectResponse("/customers/add", status_code=303)
    _customer_plans.pop(plan_id, None)
    ok, result = customers.import_rows(entry[3], user['id'])
    if ok:
        flash(request, f"Импорт клиентов: добавлено {result['created']}, дополнено {result['updated']}.")
        return RedirectResponse("/customers", status_code=303)
    flash(request, f"Импорт не выполнен, ничего не изменено: {result}", "error")
    return RedirectResponse("/customers/add", status_code=303)


@app.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_page(request: Request, customer_id: int, user=Depends(current_user)):
    customer = customers.get(customer_id)
    if not customer:
        raise HTTPException(404, "Клиент не найден.")
    return render(request, "customer.html", user, c=customer)


@app.post("/customers/{customer_id}")
def customer_save(request: Request, customer_id: int, name: str = Form(""), birthday: str = Form(""),
                  note: str = Form(""), csrf: str = Form(""), user=Depends(current_user)):
    check_csrf(request, csrf)
    born = parse_birthday(birthday) if birthday.strip() else None
    if birthday.strip() and not born:
        flash(request, "День рождения — ДД.ММ или ДД.ММ.ГГГГ, например 12.05 или 12.05.1995.", "error")
        return RedirectResponse(f"/customers/{customer_id}", status_code=303)
    ok, result = customers.update(customer_id, name, born, note)
    flash(request, "Сохранено." if ok else result, "ok" if ok else "error")
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@app.post("/customers/{customer_id}/delete")
def customer_delete(request: Request, customer_id: int, csrf: str = Form(""), user=Depends(admin_only)):
    check_csrf(request, csrf)
    ok, result = customers.delete(customer_id, user['id'])
    flash(request, "Клиент удалён: номер и имя стёрты, чеки остались без привязки." if ok else result,
          "ok" if ok else "error")
    return RedirectResponse("/customers" if ok else f"/customers/{customer_id}", status_code=303)


# ---------------------------------------------------------------- фискализация

FISCAL_FILTERS = (("problem", "Непробитые"), ("", "Все"), ("done", "Пробиты"), ("manual", "Вручную"))


@app.get("/fiscal", response_class=HTMLResponse)
def fiscal_page(request: Request, status: str = "problem", store: int = 0, user=Depends(current_user)):
    status = status if status in dict(FISCAL_FILTERS) else "problem"
    return render(request, "fiscal.html", user, rows=fiscal.list_receipts(status or None, store or None) or [],
                  counts=fiscal.counts() or {}, boxes=fiscal.cashboxes() or [], status=status, store=store or None,
                  filters=FISCAL_FILTERS, report=request.session.pop("fiscal_report", None))


@app.post("/fiscal/cashbox")
def fiscal_cashbox(request: Request, store_id: int = Form(...), cashbox: str = Form(""), csrf: str = Form(""),
                   user=Depends(admin_only)):
    check_csrf(request, csrf)
    ok, result = fiscal.set_cashbox(store_id, cashbox)
    flash(request, (f"Касса магазина: {result}." if result else "Касса у магазина убрана: его операции не фискализируются.")
          if ok else result, "ok" if ok else "error")
    return RedirectResponse("/fiscal", status_code=303)


@app.post("/fiscal/retry")
def fiscal_retry(request: Request, rec_id: int = Form(...), csrf: str = Form(""), user=Depends(current_user)):
    check_csrf(request, csrf)
    ok, result = fiscal.retry(rec_id)
    flash(request, "Чек снова в очереди: бот отправит его в течение минуты." if ok else result, "ok" if ok else "error")
    return RedirectResponse("/fiscal", status_code=303)


@app.post("/fiscal/manual")
def fiscal_manual(request: Request, rec_id: int = Form(...), note: str = Form(""), csrf: str = Form(""),
                  user=Depends(current_user)):
    check_csrf(request, csrf)
    ok, result = fiscal.mark_manual(rec_id, user['id'], note)
    flash(request, "Отмечено: чек оформлен вручную." if ok else result, "ok" if ok else "error")
    return RedirectResponse("/fiscal", status_code=303)


@app.post("/fiscal/report")
def fiscal_report(request: Request, store_id: int = Form(...), kind: str = Form(...), csrf: str = Form(""),
                  user=Depends(current_user)):
    check_csrf(request, csrf)
    if kind == "z":
        ok, data = fiscal.z_report(store_id, user['id'])
    else:
        ok, data = fiscal.x_report(store_id)
    if not ok:
        flash(request, data, "error")
    else:
        flash(request, "Смена закрыта (Z-отчёт)." if kind == "z" else "X-отчёт получен.", "ok")
        request.session["fiscal_report"] = {"kind": kind.upper(), "store_id": store_id,
                                            "lines": fiscal.report_lines(data)}
    return RedirectResponse("/fiscal", status_code=303)


@app.get("/marking", response_class=HTMLResponse)
def marking_page(request: Request, store: int = 0, user=Depends(current_user)):
    return _marking_page(request, user, store=store or None)


@app.post("/marking/accept", response_class=HTMLResponse)
async def marking_accept(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    article = (form.get("article") or "").strip()
    product = get_product_by_article(article) if article else None
    codes = mark_codes.split_codes(form.get("codes"))
    if not product:
        flash(request, f"Товар с артикулом «{article}» не найден." if article else "Укажите артикул товара.", "error")
        return _marking_page(request, user, accept_form={"article": article, "codes": form.get("codes") or ""})
    ok, result = marking.accept_codes(product['id'], codes, user['id'], add_to_stock=bool(form.get("add_to_stock")))
    if not ok:
        flash(request, result, "error")
        return _marking_page(request, user, accept_form={"article": article, "codes": form.get("codes") or ""})
    parts = [f"Принято кодов: {result['added']} к {product['article_number']}"
             + (f", остаток увеличен на {result['added']}" if form.get("add_to_stock") and result['added'] else "") + "."]
    if result['duplicates']:
        parts.append(f"Уже были у этого товара: {len(result['duplicates'])}.")
    flash(request, " ".join(parts), "ok")
    if result['foreign']:
        flash(request, "Коды другого товара, не приняты: " + ", ".join(result['foreign'][:10]), "error")
    if result['invalid']:
        flash(request, "Не похожи на код маркировки: " + ", ".join(result['invalid'][:10]), "error")
    return RedirectResponse(f"/marking/product/{product['id']}", status_code=303)


@app.get("/marking/product/{product_id}", response_class=HTMLResponse)
def marking_product(request: Request, product_id: int, user=Depends(current_user)):
    product = get_product_by_id(product_id)
    if not product:
        raise HTTPException(404, "Товар не найден.")
    codes = marking.product_codes(product_id) or []
    in_stock = sum(1 for c in codes if c['status'] in ('in_stock', 'returned'))
    return render(request, "marking_product.html", user, product=product, codes=codes, in_stock=in_stock)


@app.post("/marking/write-off")
def marking_write_off(request: Request, code_id: int = Form(...), reason: str = Form(""), csrf: str = Form(""),
                      user=Depends(current_user)):
    check_csrf(request, csrf)
    ok, result = marking.write_off(code_id, user['id'], reason)
    flash(request, "Код списан, остаток уменьшен на 1." if ok else result, "ok" if ok else "error")
    if ok:
        return RedirectResponse(f"/marking/product/{result}", status_code=303)
    return RedirectResponse(request.headers.get("referer") or "/marking", status_code=303)


@app.post("/marking/reconcile", response_class=HTMLResponse)
async def marking_reconcile(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    store = parse_int(form.get("store") or "") or None
    codes = mark_codes.split_codes(form.get("codes"))
    if not codes:
        flash(request, "Отсканируйте коды изделий на витрине.", "error")
        return _marking_page(request, user, store=store)
    result = marking.reconcile(codes, store)
    if result is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return _marking_page(request, user, store=store, reconcile=result, scanned=len(codes))


@app.get("/stock.xlsx")
def stock_xlsx(user=Depends(current_user)):
    items = get_stock_report_data()
    if items is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return StreamingResponse(build_stock_excel(items), media_type=XLSX,
                             headers={"Content-Disposition": f'attachment; filename="stock_{now():%Y%m%d_%H%M}.xlsx"'})


# ---------------------------------------------------------------- импорт

def _drop_plan(plan):
    """Закрыть и удалить временный архив с фото, если он был."""
    archive = getattr(plan, "archive", None)
    if archive is not None:
        archive.close()
        Path(archive.zf.filename).unlink(missing_ok=True)


def _cleanup_plans():
    cutoff = time.time() - IMPORT_TTL
    for key in [k for k, (_, created, _) in _import_plans.items() if created < cutoff]:
        _drop_plan(_import_plans.pop(key)[2])


IMPORT_MAX = 10 * 1024 * 1024
IMPORT_ZIP_MAX = 500 * 1024 * 1024


async def _save_upload(upload, limit):
    """Загруженный архив → временный файл в DATA_DIR/import. None, если больше limit."""
    folder = Path(config.DATA_DIR) / "import"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{secrets.token_hex(8)}.zip"
    size = 0
    with path.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                return None
            out.write(chunk)
    return path


@app.get("/import", response_class=HTMLResponse)
def import_form(request: Request, user=Depends(current_user)):
    return render(request, "import.html", user, stores=get_stores() or [])


@app.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request, file: UploadFile = File(...), store: str = Form(""), csrf: str = Form(""),
                        user=Depends(current_user)):
    check_csrf(request, csrf)
    archive = None
    default_store = parse_int(store) if store else None
    try:
        if (file.filename or "").lower().endswith(".zip"):
            path = await _save_upload(file, IMPORT_ZIP_MAX)
            if not path:
                raise ValueError(f"Архив больше {IMPORT_ZIP_MAX // 1024 // 1024} МБ — разделите его на части.")
            try:
                archive = PhotoArchive(path)
            except ArchiveError:
                path.unlink(missing_ok=True)
                raise
            data, name = archive.table()
        else:
            data, name = await file.read(IMPORT_MAX + 1), file.filename or ""
            if len(data) > IMPORT_MAX:
                raise ValueError("Файл больше 10 МБ.")
        plan = await run_in_threadpool(build_plan, data, name, default_store, archive)
    except ValueError as e:
        if archive is not None:
            archive.close()
            Path(archive.zf.filename).unlink(missing_ok=True)
        return render(request, "import.html", user, stores=get_stores() or [], error=str(e))
    _cleanup_plans()
    plan_id = secrets.token_urlsafe(12)
    _import_plans[plan_id] = (user['id'], time.time(), plan)
    return render(request, "import_preview.html", user, plan=plan, plan_id=plan_id, titles=FIELD_TITLES,
                  sample=plan.rows[:20], filename=file.filename)


@app.post("/import/{plan_id}")
async def import_apply(request: Request, plan_id: str, mode: str = Form("all"), csrf: str = Form(""),
                       user=Depends(current_user)):
    check_csrf(request, csrf)
    entry = _import_plans.pop(plan_id, None)
    if not entry or entry[0] != user['id']:
        flash(request, "Предпросмотр устарел, загрузите файл ещё раз.", "error")
        return RedirectResponse("/import", status_code=303)
    plan = entry[2]
    try:
        ok, result = apply_plan(plan, update_existing=mode == "all")
        if ok:
            flash(request, f"Импорт завершён: добавлено {result['created']}, обновлено {result['updated']}"
                           + (f", пропущено {result['skipped']}" if result['skipped'] else "") + ".")
            added, errors = await run_in_threadpool(attach_photos, plan, user['id'])
            if added:
                flash(request, f"Добавлено фото: {added}.")
            if errors:
                flash(request, f"Не загружено фото: {len(errors)} — " + "; ".join(errors[:5]), "error")
            return RedirectResponse("/products", status_code=303)
    finally:
        _drop_plan(plan)
    flash(request, f"Импорт не выполнен, ничего не изменено: {result}", "error")
    return RedirectResponse("/import", status_code=303)


# ---------------------------------------------------------------- ценники

@app.get("/labels", response_class=HTMLResponse)
def labels_form(request: Request, articles: str = "", user=Depends(current_user)):
    return render(request, "labels.html", user, stores=get_stores() or [], articles=articles,
                  width=config.LABEL_WIDTH_MM, height=config.LABEL_HEIGHT_MM, since=today() - timedelta(days=6))


@app.post("/labels")
async def labels_pdf(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    mode = form.get("mode", "articles")
    if mode == "articles":
        articles = list(dict.fromkeys(a for a in re.split(r"[\s,;]+", form.get("articles", "")) if a))
        products = get_products_by_articles(articles[:1000]) or []
    elif mode == "arrived":
        products = get_products_for_labels(arrived_from=parse_date(form.get("since", "")) or today(),
                                           store=parse_int(form.get("store") or ""))
    else:
        products = get_products_for_labels(store=parse_int(form.get("store") or ""))
    width = parse_float(form.get("width") or "") or config.LABEL_WIDTH_MM
    height = parse_float(form.get("height") or "") or config.LABEL_HEIGHT_MM
    if not (15 <= width <= 120 and 10 <= height <= 120):
        flash(request, "Размер этикетки: ширина 15–120 мм, высота 10–120 мм.", "error")
        return RedirectResponse("/labels", status_code=303)
    if not products:
        flash(request, "Подходящих товаров не найдено.", "error")
        return RedirectResponse("/labels", status_code=303)
    pdf, count = generate_labels_pdf(products, copies=form.get("copies", "one"), width_mm=width, height_mm=height)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="labels_{now():%Y%m%d_%H%M}.pdf"'})


# ---------------------------------------------------------------- отчёты

def _store_filter(store):
    store_id = parse_int(store) if store else None
    stores = get_stores(active_only=False) or []
    name = next((st['name'] for st in stores if st['id'] == store_id), None)
    return (store_id, name) if name else (None, None)


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, start: str = "", end: str = "", store: str = "", user=Depends(current_user)):
    d = today()
    start_d = parse_date(start) or d.replace(day=1)
    end_d = parse_date(end) or d
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    store_id, store_name = _store_filter(store)
    ops = get_period_operations(*day_bounds(start_d, end_d), store_id)
    if ops is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return render(request, "reports.html", user, start=start_d, end=end_d, s=build_summary(ops), ops=ops,
                  payment_types=PAYMENT_TYPES, stores=get_stores() or [], store=store_id,
                  breakdown=[] if store_id else store_breakdown(ops))


@app.get("/reports.xlsx")
def reports_xlsx(start: str = "", end: str = "", store: str = "", user=Depends(current_user)):
    d = today()
    start_d, end_d = parse_date(start) or d, parse_date(end) or d
    store_id, store_name = _store_filter(store)
    ops = get_period_operations(*day_bounds(start_d, end_d), store_id)
    if ops is None:
        raise HTTPException(500, "Ошибка базы данных.")
    excel = build_excel(ops, build_summary(ops), start_d, end_d, store_name=store_name,
                        breakdown=[] if store_id else store_breakdown(ops))
    return StreamingResponse(excel, media_type=XLSX,
                             headers={"Content-Disposition": f'attachment; filename="report_{start_d:%Y%m%d}_{end_d:%Y%m%d}.xlsx"'})


# ---------------------------------------------------------------- аналитика

def _analytics_filters(start, end, store, ptype, metal, seller):
    d0, d1 = analytics.default_period()
    store_id, _ = _store_filter(store)
    users = {u['id']: u for u in get_all_users() or []}
    seller_id = parse_int(seller) if seller else None
    return analytics.Filters(parse_date(start) or d0, parse_date(end) or d1, store_id,
                             ptype if ptype in PRODUCT_TYPES else None, metal if metal in METAL_TYPES else None,
                             seller_id if seller_id in users else None), users


def _filter_titles(f, users):
    stores = {st['id']: st['name'] for st in get_stores(active_only=False) or []}
    period = f"{f.start:%d.%m.%Y} — {f.end:%d.%m.%Y}"
    return [("Период", period), ("Магазин", stores.get(f.store_id, "Все магазины")),
            ("Тип изделия", f.product_type or "Все"), ("Металл", f.metal or "Все"),
            ("Продавец", users[f.user_id]['name'] if f.user_id else "Все")]


def _chart_data(data):
    """Цифры для графиков (Decimal → float)."""
    t, st = data['trend'], data['structure']
    num = lambda xs: [round(float(x), 2) for x in xs]  # noqa: E731
    pie = lambda gs: {"labels": [str(g['name']) for g in gs], "values": num(g['revenue'] for g in gs)}  # noqa: E731
    return {"labels": t['labels'], "revenue": num(t['revenue']), "previous": num(t.get('previous', [])),
            "receipts": t['receipts'], "avg_check": num(t['avg_check']),
            "types": pie(st['types']), "metals": pie(st['metals']), "payments": pie(st['payments']),
            "stores": pie(st['stores']),
            "clients": {"labels": data['clients']['trend']['labels'],
                        **{k: num(data['clients']['trend'][k]) for k in ("new", "back", "anon")}}}


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, start: str = "", end: str = "", store: str = "", type: str = "", metal: str = "",
                   seller: str = "", user=Depends(current_user)):
    f, users = _analytics_filters(start, end, store, type, metal, seller)
    data = analytics.build(f)
    if data is None:
        raise HTTPException(500, "Ошибка базы данных.")
    sellers = sorted((u for u in users.values() if u['role'] in CASH_ROLES), key=lambda u: u['name'])
    return render(request, "analytics.html", user, d=data, f=f, chart=_chart_data(data), query=urlencode(f.query()),
                  stores=get_stores() or [], types=PRODUCT_TYPES, metals=METAL_TYPES, sellers=sellers)


@app.get("/analytics.xlsx")
def analytics_xlsx(start: str = "", end: str = "", store: str = "", type: str = "", metal: str = "", seller: str = "",
                   user=Depends(current_user)):
    f, users = _analytics_filters(start, end, store, type, metal, seller)
    data = analytics.build(f)
    if data is None:
        raise HTTPException(500, "Ошибка базы данных.")
    excel = build_analytics_excel(data, _filter_titles(f, users))
    return StreamingResponse(excel, media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="analytics_{f.start:%Y%m%d}_{f.end:%Y%m%d}.xlsx"'})


# ---------------------------------------------------------------- магазины

@app.get("/stores", response_class=HTMLResponse)
def stores_page(request: Request, user=Depends(admin_only)):
    return render(request, "stores.html", user, stores=get_stores(active_only=False) or [], counts=get_store_counts() or {})


@app.post("/stores")
async def stores_action(request: Request, user=Depends(admin_only)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if form.get("action") == "add":
        ok, msg = create_store(form.get("name"), form.get("address"))
    else:
        store_id = parse_int(form.get("store_id") or "")
        ok, msg = update_store(store_id, form.get("name"), form.get("address"), bool(form.get("is_active")))
    flash(request, msg, "ok" if ok else "error")
    return RedirectResponse("/stores", status_code=303)


# ---------------------------------------------------------------- инвентаризация

def _inventory_or_404(inventory_id, user):
    inv = inventory.get(inventory_id)
    if not inv:
        raise HTTPException(404, "Инвентаризация не найдена.")
    if not inventory.can_use_store(user, inv['store_id']):
        raise HTTPException(403, "Инвентаризация другого магазина.")
    return inv


@app.get("/inventory", response_class=HTMLResponse)
def inventory_list(request: Request, user=Depends(current_user)):
    stores = [st for st in get_stores() or [] if inventory.can_use_store(user, st['id'])]
    return render(request, "inventory_list.html", user, stores=stores, rows=inventory.recent(own_store(user)) or [],
                  default_store=user.get('store_id'))


@app.post("/inventory")
def inventory_start(request: Request, store_id: str = Form(""), csrf: str = Form(""), user=Depends(current_user)):
    check_csrf(request, csrf)
    store = parse_int(store_id)
    if not store or not inventory.can_use_store(user, store):
        flash(request, "Выберите магазин.", "error")
        return RedirectResponse("/inventory", status_code=303)
    ok, result = inventory.start(store, user['id'])
    if not ok:
        flash(request, result, "error")
        return RedirectResponse("/inventory", status_code=303)
    return RedirectResponse(f"/inventory/{result}", status_code=303)


@app.get("/inventory/{inventory_id}", response_class=HTMLResponse)
def inventory_page(request: Request, inventory_id: int, user=Depends(current_user)):
    inv = _inventory_or_404(inventory_id, user)
    return render(request, "inventory.html", user, inv=inv, scans=inventory.scans(inventory_id) or [])


@app.get("/inventory/{inventory_id}/summary", response_class=HTMLResponse)
def inventory_summary(request: Request, inventory_id: int, user=Depends(current_user)):
    inv = _inventory_or_404(inventory_id, user)
    summary = inventory.summary(inventory_id)
    if summary is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return render(request, "inventory_summary.html", user, inv=inv, s=summary, limit=inventory.LIST_LIMIT)


async def _json_object(request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "Неверный запрос.")
    return body


@app.post("/api/inventory/{inventory_id}/scan")
async def inventory_scan(request: Request, inventory_id: int, user=Depends(current_user)):
    check_csrf(request, request.headers.get("x-csrf", ""))
    _inventory_or_404(inventory_id, user)
    body = await _json_object(request)
    ok, result = inventory.scan(inventory_id, str(body.get("code") or ""), user['id'])
    if not ok:
        raise HTTPException(400, result)
    return result


@app.post("/api/inventory/{inventory_id}/unscan")
async def inventory_unscan(request: Request, inventory_id: int, user=Depends(current_user)):
    check_csrf(request, request.headers.get("x-csrf", ""))
    _inventory_or_404(inventory_id, user)
    body = await _json_object(request)
    ok, result = inventory.unscan(inventory_id, parse_int(str(body.get("scan_id") or "")) or 0)
    if not ok:
        raise HTTPException(400, result)
    return result


@app.post("/inventory/{inventory_id}/apply")
def inventory_apply(request: Request, inventory_id: int, csrf: str = Form(""), zero_missing: str = Form(""),
                    user=Depends(current_user)):
    check_csrf(request, csrf)
    _inventory_or_404(inventory_id, user)
    ok, result = inventory.apply(inventory_id, user['id'], zero_missing=bool(zero_missing))
    if ok:
        flash(request, f"Инвентаризация применена: в наличии {result['found']} изделий"
                       + (f", из них перенесено из других магазинов {result['moved']}" if result['moved'] else "")
                       + (f"; остаток обнулён у {result['zeroed']} ненайденных" if result['zeroed'] else "") + ".")
    else:
        flash(request, result, "error")
    return RedirectResponse(f"/inventory/{inventory_id}", status_code=303)


@app.post("/inventory/{inventory_id}/cancel")
def inventory_cancel(request: Request, inventory_id: int, csrf: str = Form(""), user=Depends(current_user)):
    check_csrf(request, csrf)
    _inventory_or_404(inventory_id, user)
    ok, result = inventory.cancel(inventory_id, user['id'])
    flash(request, "Инвентаризация отменена, остатки не менялись." if ok else result, "ok" if ok else "error")
    return RedirectResponse("/inventory", status_code=303)


# ---------------------------------------------------------------- внесение денег в кассу

@app.get("/cash", response_class=HTMLResponse)
def cash_page(request: Request, user=Depends(current_user)):
    stores = get_stores() or []
    default = user.get('store_id') if user.get('store_id') in {st['id'] for st in stores} else None
    if default is None and len(stores) == 1:
        default = stores[0]['id']
    return render(request, "cash.html", user, stores=stores, default_store=default,
                  operations=recent_cash_operations(30) or [], fiscal_on=bool(config.FISCAL_ENABLED))


@app.post("/cash")
async def cash_deposit(request: Request, user=Depends(current_user)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    amount = parse_money(str(form.get("amount") or ""))
    store_id = parse_int(str(form.get("store_id") or ""))
    if amount is None or amount <= 0:
        flash(request, "Введите сумму внесения больше нуля.", "error")
        return RedirectResponse("/cash", status_code=303)
    ok, result = create_cash_deposit_record(amount, form.get("comment"), user['id'], store_id)
    if ok:
        store = next((st['name'] for st in get_stores(active_only=False) or [] if st['id'] == store_id), "")
        logger.info("Внесение #%s на %s в магазин %s (%s)", result, amount, store_id, user['id'])
        flash(request, f"Внесение №{result}: {money(amount)} в кассу «{store}».")
    else:
        flash(request, result, "error")
    return RedirectResponse("/cash", status_code=303)


# ---------------------------------------------------------------- персонал

ROLE_BY_KEY = {'cashier': 'cashier', 'manager': 'manager', 'admin': 'admin'}


@app.get("/staff", response_class=HTMLResponse)
def staff(request: Request, user=Depends(admin_only)):
    return render(request, "staff.html", user, users=get_all_users() or [], stores=get_stores() or [])


@app.post("/staff")
async def staff_action(request: Request, user=Depends(admin_only)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    action = form.get("action")
    target = parse_int(form.get("user_id") or "")
    if action == "add":
        phone = normalize_phone(form.get("phone"))
        role = ROLE_BY_KEY.get(form.get("role"))
        name = (form.get("name") or "").strip()[:255]
        if not phone or not role or len(name) < 2:
            flash(request, "Заполните имя, телефон (+7XXXXXXXXXX) и роль.", "error")
        else:
            ok, msg = add_user_to_db(phone, role, name, parse_int(form.get("store_id") or "") or None)
            flash(request, msg, "ok" if ok else "error")
    elif target == user['id'] and action in ("role", "deactivate"):
        flash(request, "Нельзя изменить собственную роль или деактивировать себя.", "error")
    elif action == "role" and target:
        ok, msg = change_user_role_function(target, ROLE_BY_KEY.get(form.get("role"), ""))
        flash(request, msg, "ok" if ok else "error")
    elif action == "deactivate" and target:
        ok, msg = deactivate_user_in_db(target)
        flash(request, msg, "ok" if ok else "error")
    elif action == "store" and target:
        ok, msg = set_user_store(target, parse_int(form.get("store_id") or "") or None)
        flash(request, msg, "ok" if ok else "error")
    elif action == "phone" and target:
        ok, msg = update_user_phone(target, form.get("phone") or "")
        flash(request, msg + (" Сотруднику нужно заново войти в бот через /start." if ok else ""), "ok" if ok else "error")
    return RedirectResponse("/staff", status_code=303)


# ---------------------------------------------------------------- Mini App (касса)

def app_user(request: Request):
    tg_user = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not tg_user:
        raise HTTPException(401, "Откройте кассу из Telegram-бота.")
    user = get_user_by_chat_id(tg_user.get('id'))
    if not user or user.get('role') not in CASH_ROLES:
        raise HTTPException(403, "Сначала войдите в бота: /start → «Поделиться номером».")
    return user


def _product_json(p):
    return {"id": p['id'], "article": p['article_number'], "model": p['model'], "type": p['product_type'],
            "details": details_line(p), "inserts": p.get('inserts'), "store": p['Store'],
            "price": int(p['price']), "min_price": int(p['min_price']), "quantity": p['quantity'],
            **_photo_urls(p['id'])}


def _photo_urls(product_id):
    photo = (photos.main_photos([product_id]) or {}).get(product_id)
    return {"photo": photos.signed_url(photo), "photo_full": photos.signed_url(photo, thumb=False)} if photo else {}


# ---------------------------------------------------------------- касса для ПК (web/kassa.py)

app.include_router(kassa.router)


@app.get("/kassa", response_class=HTMLResponse)
def kassa_page(request: Request):
    if not settings_store.is_configured():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "kassa.html", {"org": config.ORG_NAME or "POS"})


@app.get("/app", response_class=HTMLResponse)
def mini_app(request: Request):
    return templates.TemplateResponse(request, "app.html", {"payment_types": PAYMENT_TYPES})


@app.get("/api/app/me")
def api_me(user=Depends(app_user)):
    start_dt, end_dt = day_bounds(today(), today())
    sales = get_today_sales_by_user(user['id'], start_dt, end_dt) or []
    return {"name": user['name'], "role": user['role'],
            "today": [{"id": s['id'], "time": s['sale_date'].strftime('%H:%M'), "article": s['article_number'],
                       "model": s['model'], "price": int(s['sold_price']), "payment": s['payment_type']} for s in sales]}


@app.get("/api/app/product")
def api_product(article: str, user=Depends(app_user)):
    article = article.strip()
    product = get_product_by_article(article)
    if not product and article.isdigit() and article.lstrip("0") and article.lstrip("0") != article:
        product = get_product_by_article(article.lstrip("0"))      # штрихкод дополнен нулями слева (EAN)
    if not product:
        raise HTTPException(404, f"Артикул {article} не найден.")
    store_error = foreign_store_error(user, product.get('store_id'), product.get('Store'))
    if store_error:
        raise HTTPException(400, store_error)
    if marking_required(product):
        raise HTTPException(400, "Маркируемый товар: продажа по коду маркировки — на кассе ПК со сканером или в боте.")
    return _product_json(product)


@app.post("/api/app/sale")
async def api_sale(request: Request, user=Depends(app_user)):
    body = await request.json()
    product = get_product_by_article(str(body.get("article", "")).strip())
    if not product:
        raise HTTPException(404, "Товар не найден.")
    price = parse_money(str(body.get("price", "")))
    payment = body.get("payment")
    if payment not in PAYMENT_TYPES:
        raise HTTPException(400, "Выберите способ оплаты.")
    if price is None or price <= 0:
        raise HTTPException(400, "Введите сумму продажи.")
    price = price.quantize(Decimal('1'))
    if price < (product['min_price'] or 0):
        raise HTTPException(400, f"Сумма ниже минимальной цены {money(product['min_price'])}.")
    if (product['quantity'] or 0) <= 0:
        raise HTTPException(400, "Товара нет в наличии.")
    if marking_required(product):
        raise HTTPException(400, "Маркируемый товар: продажа по коду маркировки — на кассе ПК со сканером или в боте.")
    ok, result = record_sale(product['id'], product['article_number'], user['id'], price, payment)
    if not ok:
        raise HTTPException(400, result)
    logger.info("Mini App: продажа #%s, %s за %s (%s)", result, product['article_number'], price, user['id'])
    return {"sale_id": result, "price": int(price), "payment": payment, "model": product['model']}


# ---------------------------------------------------------------- первоначальная настройка и настройки

_setup_attempts = {}   # ip -> [время неудачных попыток]
SETUP_MAX_FAILS = 10
SETUP_FAIL_WINDOW = 600


def _setup_blocked(ip):
    recent = [t for t in _setup_attempts.get(ip, []) if time.monotonic() - t < SETUP_FAIL_WINDOW]
    _setup_attempts[ip] = recent
    return len(recent) >= SETUP_MAX_FAILS


def _suggest_web_url(request: Request):
    host_header = request.headers.get("host") or ""
    host = host_header.split(":")[0]
    if config.LOCAL_MODE:
        # ПК в магазине: адрес в локальной сети. Открыли мастер на самом ПК (localhost) — адрес от установщика
        if host in ("localhost", "127.0.0.1", "testserver") or not host:
            return os.environ.get("POSBOT_LAN_URL", "")
        return f"http://{host_header}"
    if not host or host in ("localhost", "testserver") or re.match(r"^[\d.]+$", host):
        return ""
    return f"https://{host}"


def _setup_page(request: Request, status=200, errors=(), form=None, **extra):
    form = form or {}
    overrides = {k: form.get(k, "") for k in settings_form.SETUP_KEYS if k in form}
    if not config.WEB_URL and "WEB_URL" not in overrides:
        overrides["WEB_URL"] = _suggest_web_url(request)
    stores = get_stores(active_only=False) or []
    return templates.TemplateResponse(request, "setup.html", {
        "user": None, "messages": [["error", e] for e in errors], "csrf": csrf_token(request),
        "groups": settings_form.build_groups(settings_form.SETUP_KEYS, overrides), "existing_stores": stores,
        "form": form, "has_admin": _has_admin(), **extra}, status_code=status)


def _has_admin():
    return any(u.get('role') == 'admin' for u in (get_all_users() or []))


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if settings_store.is_configured():
        return RedirectResponse("/settings", status_code=303)
    settings_store.ensure_setup_code()
    return _setup_page(request)


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(request: Request):
    if settings_store.is_configured():
        return RedirectResponse("/settings", status_code=303)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    ip = request.client.host if request.client else "?"
    if _setup_blocked(ip):
        raise HTTPException(429, "Слишком много неверных попыток. Подождите 10 минут.")
    if not settings_store.check_setup_code(form.get("code")):
        _setup_attempts.setdefault(ip, []).append(time.monotonic())
        logger.warning("Мастер настройки: неверный код (%s)", ip)
        return _setup_page(request, 403, ["Неверный код настройки. Узнать его на сервере: "
                                          "docker compose exec web python -m setup_code"], dict(form))

    changes, errors = settings_form.collect(form, settings_form.SETUP_KEYS)
    errors += settings_form.fiscal_errors(changes)
    if not changes.get("TOKEN") and not config.TOKEN:
        errors.append("Укажите токен бота.")
    stores = []
    if not (get_stores(active_only=False) or []):
        stores = list(dict.fromkeys(n.strip() for n in (form.get("stores") or "").splitlines() if n.strip()))
        if not stores:
            errors.append("Укажите хотя бы один магазин.")
        elif any(len(n) < 2 or len(n) > 100 for n in stores):
            errors.append("Название магазина — от 2 до 100 символов.")
    admin_phone = normalize_phone(form.get("admin_phone"))
    admin_name = (form.get("admin_name") or "").strip()[:255]
    if form.get("admin_phone") or not _has_admin():
        if not admin_phone:
            errors.append("Телефон администратора: казахстанский номер, например +77011234567.")
        if len(admin_name) < 2:
            errors.append("Укажите имя администратора.")
    bot_name = None
    if not errors:
        token_error, bot_name = await run_in_threadpool(settings_form.check_token_change, changes)
        if token_error:
            errors.append(token_error)
    if errors:
        return _setup_page(request, 400, errors, dict(form))

    ok, result = settings_store.save(changes)
    for name in stores:
        if ok:
            ok, result = create_store(name)
    if ok and admin_phone:
        ok, result = ensure_admin(admin_phone, admin_name)
    if ok:
        ok, result = settings_store.finish_setup()
    if not ok:
        return _setup_page(request, 500, [str(result)], dict(form))
    logger.info("Первоначальная настройка завершена (%s).", ip)
    demo = None
    if form.get("demo"):
        demo_ok, demo = await run_in_threadpool(demo_data.seed_demo)
        if not demo_ok:
            logger.warning("Демо-данные не добавлены: %s", demo)
            demo = {"error": demo}
    return templates.TemplateResponse(request, "setup_done.html", {
        "user": None, "messages": [], "bot_name": bot_name, "admin_phone": admin_phone,
        "web_url": config.WEB_URL, "demo": demo})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(admin_only)):
    return render(request, "settings.html", user, groups=settings_form.build_groups(), demo=demo_data.demo_counts(),
                  changes=settings_store.recent_changes() or [], titles={s.key: s.title for s in config.SETTINGS})


@app.post("/settings", response_class=HTMLResponse)
async def settings_submit(request: Request, user=Depends(admin_only)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    keys = [s.key for s in config.SETTINGS if not s.hidden]
    changes, errors = settings_form.collect(form, keys)
    errors += settings_form.fiscal_errors(changes)
    if "TOKEN" in changes and changes["TOKEN"] is None:
        errors.append("Токен бота нельзя удалить — только заменить.")
    if not errors:
        token_error, _ = await run_in_threadpool(settings_form.check_token_change, changes)
        if token_error:
            errors.append(token_error)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse("/settings", status_code=303)
    ok, result = settings_store.save(changes, user['id'])
    if not ok:
        flash(request, result, "error")
    elif not result:
        flash(request, "Изменений нет.")
    else:
        restart = [k for k in result if config.BY_KEY[k].restart]
        flash(request, "Сохранено: " + ", ".join(config.BY_KEY[k].title for k in result) + "."
              + (" Бот перезапустится в течение минуты." if restart else ""))
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/demo", response_class=HTMLResponse)
def settings_demo(request: Request, action: str = Form(...), csrf: str = Form(""), user=Depends(admin_only)):
    check_csrf(request, csrf)
    if action == "add":
        ok, result = demo_data.seed_demo()
        flash(request, f"Добавлены демо-данные: {result['products']} товаров, {result['sales']} продаж за две недели, "
              f"{result['customers']} клиентов с историей покупок за год." if ok else result, "ok" if ok else "error")
    elif action == "customers":
        ok, result = demo_data.seed_demo_customers()
        flash(request, f"Добавлены демо-клиенты: {result['customers']}, покупок {result['purchases']} за год. "
              "Смотрите «Аналитика → Клиенты» и «Клиенты»." if ok else result, "ok" if ok else "error")
    elif action == "remove":
        ok, result = demo_data.remove_demo()
        flash(request, f"Демо-данные удалены: {result['products']} товаров, {result['operations']} операций, "
              f"{result['customers']} клиентов."
              if ok else result, "ok" if ok else "error")
    else:
        raise HTTPException(400, "Неизвестное действие.")
    logger.info("Демо-данные: %s (%s) пользователем %s", action, "ok" if ok else result, user['id'])
    return RedirectResponse("/settings#demo", status_code=303)


@app.get("/caddy/ask")
def caddy_ask(domain: str = ""):
    """Caddy спрашивает, можно ли выпустить сертификат для домена (on-demand TLS).
    До настройки — для любого домена (иначе /setup не открыть по HTTPS), после — только для адреса из настроек."""
    domain = domain.strip().lower().rstrip(".")
    if not re.match(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$", domain):
        return Response(status_code=404)
    if config.WEB_URL:
        allowed = re.sub(r"^https?://", "", config.WEB_URL).split("/")[0].split(":")[0].lower()
        return Response(status_code=200 if domain == allowed else 404)
    return Response(status_code=200 if not settings_store.is_configured() else 404)
