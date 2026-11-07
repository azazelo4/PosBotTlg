"""Веб-админка и Telegram Mini App поверх той же БД, что и бот.

Запуск: uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
"""
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import jinja2
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

import config
import settings_store
from buttons import PAYMENT_TYPES, ROLE_TITLES
from database import (ensure_admin, get_connection, get_stores, get_store_counts, create_store, update_store, set_user_store, init_pool, get_user_by_id, get_user_by_chat_id, search_products, get_product_by_id,
                      get_product_by_article, get_products_by_articles, get_products_for_labels, add_product_to_db,
                      update_product, get_stock_report_data, get_low_stock_products, get_period_operations,
                      record_sale, get_today_sales_by_user, get_all_users, add_user_to_db, change_user_role_function,
                      deactivate_user_in_db, update_user_phone)
from schema import METAL_TYPES, PRODUCT_TYPES, ensure_schema
from utils.catalog_import import build_plan, apply_plan, FIELD_TITLES
from utils.common import money, today, now, parse_money, parse_float, parse_int, parse_date, normalize_phone, day_bounds
from utils.labels import generate_labels_pdf, details_line
from utils.report_generator import build_summary, build_excel, store_breakdown
from utils.stock_report import build_stock_excel, stock_totals
from web import settings_form, kassa
from web.security import (session_secret, redeem_login_token, validate_init_data, new_csrf, ADMIN_ROLES, CASH_ROLES)

logger = logging.getLogger("posbot.web")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters['money'] = money


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


@app.get("/health")
def health():
    return {"ok": True, "configured": settings_store.is_configured(), "version": config.VERSION}


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
                  totals=stock_totals(items), today=d, payment_types=PAYMENT_TYPES)


# ---------------------------------------------------------------- товары

PAGE_SIZE = 50


@app.get("/products", response_class=HTMLResponse)
def products(request: Request, q: str = "", store: str = "", stock: str = "", page: int = 1, user=Depends(current_user)):
    in_stock = {"yes": True, "no": False}.get(stock)
    page = max(page, 1)
    store_id = parse_int(store) if store else None
    rows, total = search_products(q.strip() or None, store_id, in_stock, PAGE_SIZE, (page - 1) * PAGE_SIZE) or ([], 0)
    return render(request, "products.html", user, rows=rows, total=total, q=q, store=store_id, stock=stock, page=page,
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
    if data.get('price') is not None and data.get('min_price') is not None and data['min_price'] > data['price']:
        errors.append("Минимальная цена не может быть выше цены.")
    qty = parse_int(form.get('quantity') or '0')
    if qty is None or qty < 0:
        errors.append("Остаток — целое число от 0.")
    data['quantity'] = qty or 0
    data['track_stock'] = 1 if form.get('track_stock') else 0
    arrival = parse_date(form.get('arrival_date') or '')
    if form.get('arrival_date') and not arrival:
        errors.append("Дата поступления: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.")
    data['arrival_date'] = arrival or today()
    return data, errors


def _form_ctx(product=None):
    return {"product": product or {}, "stores": get_stores() or [], "types": PRODUCT_TYPES, "metals": METAL_TYPES}


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
            return RedirectResponse(f"/products/{product_id}", status_code=303)
        errors.append(msg)
    data['id'] = product_id
    return render(request, "product_form.html", user, errors=errors, **_form_ctx(data))


@app.get("/stock.xlsx")
def stock_xlsx(user=Depends(current_user)):
    items = get_stock_report_data()
    if items is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return StreamingResponse(build_stock_excel(items), media_type=XLSX,
                             headers={"Content-Disposition": f'attachment; filename="stock_{now():%Y%m%d_%H%M}.xlsx"'})


# ---------------------------------------------------------------- импорт

def _cleanup_plans():
    cutoff = time.time() - IMPORT_TTL
    for key in [k for k, (_, created, _) in _import_plans.items() if created < cutoff]:
        _import_plans.pop(key, None)


@app.get("/import", response_class=HTMLResponse)
def import_form(request: Request, user=Depends(current_user)):
    return render(request, "import.html", user, stores=get_stores() or [])


@app.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request, file: UploadFile = File(...), store: str = Form(""), csrf: str = Form(""),
                        user=Depends(current_user)):
    check_csrf(request, csrf)
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return render(request, "import.html", user, stores=get_stores() or [], error="Файл больше 10 МБ.")
    try:
        plan = build_plan(data, file.filename or "", default_store=parse_int(store) if store else None)
    except ValueError as e:
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
    ok, result = apply_plan(entry[2], update_existing=mode == "all")
    if ok:
        flash(request, f"Импорт завершён: добавлено {result['created']}, обновлено {result['updated']}"
                       + (f", пропущено {result['skipped']}" if result['skipped'] else "") + ".")
        return RedirectResponse("/products", status_code=303)
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
            "price": int(p['price']), "min_price": int(p['min_price']), "quantity": p['quantity']}


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
    product = get_product_by_article(article.strip())
    if not product:
        raise HTTPException(404, f"Артикул {article.strip()} не найден.")
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
    host = (request.headers.get("host") or "").split(":")[0]
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
    return templates.TemplateResponse(request, "setup_done.html", {
        "user": None, "messages": [], "bot_name": bot_name, "admin_phone": admin_phone,
        "web_url": config.WEB_URL})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(admin_only)):
    return render(request, "settings.html", user, groups=settings_form.build_groups(),
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
