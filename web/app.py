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
from starlette.middleware.sessions import SessionMiddleware

import config
from buttons import PAYMENT_TYPES, ROLE_TITLES
from database import (init_pool, get_user_by_id, get_user_by_chat_id, search_products, get_product_by_id,
                      get_product_by_article, get_products_by_articles, get_products_for_labels, add_product_to_db,
                      update_product, get_stock_report_data, get_low_stock_products, get_period_operations,
                      record_sale, get_today_sales_by_user, get_all_users, add_user_to_db, change_user_role_function,
                      deactivate_user_in_db, update_user_phone)
from schema import STORES, METAL_TYPES, PRODUCT_TYPES
from utils.catalog_import import build_plan, apply_plan, FIELD_TITLES
from utils.common import money, today, now, parse_money, parse_float, parse_int, parse_date, normalize_phone, day_bounds
from utils.labels import generate_labels_pdf, details_line
from utils.report_generator import build_summary, build_excel
from utils.stock_report import build_stock_excel, stock_totals
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

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@asynccontextmanager
async def lifespan(_app):
    init_pool()
    yield


app = FastAPI(title="POS-бот", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), session_cookie="posbot_session",
                   max_age=config.WEB_SESSION_HOURS * 3600, same_site="lax",
                   https_only=bool(config.WEB_URL and config.WEB_URL.startswith("https://")))

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
    if request.url.path.startswith("/api/"):
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
    return {"ok": True}


# ---------------------------------------------------------------- дашборд

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(current_user)):
    d = today()
    periods = {}
    for key, start in (("today", d), ("month", d.replace(day=1))):
        ops = get_period_operations(*day_bounds(start, d))
        periods[key] = build_summary(ops) if ops is not None else None
    low = get_low_stock_products(config.LOW_STOCK_THRESHOLD) or []
    items = get_stock_report_data() or []
    return render(request, "dashboard.html", user, periods=periods, low_stock=low[:10], low_count=len(low),
                  totals=stock_totals(items), today=d, payment_types=PAYMENT_TYPES)


# ---------------------------------------------------------------- товары

PAGE_SIZE = 50


@app.get("/products", response_class=HTMLResponse)
def products(request: Request, q: str = "", store: str = "", stock: str = "", page: int = 1, user=Depends(current_user)):
    in_stock = {"yes": True, "no": False}.get(stock)
    page = max(page, 1)
    rows, total = search_products(q.strip() or None, store or None, in_stock, PAGE_SIZE, (page - 1) * PAGE_SIZE) or ([], 0)
    return render(request, "products.html", user, rows=rows, total=total, q=q, store=store, stock=stock, page=page,
                  pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1), stores=STORES, details=details_line)


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
    for key, options in (('Store', STORES), ('product_type', PRODUCT_TYPES), ('metal_type', METAL_TYPES)):
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
    return {"product": product or {}, "stores": STORES, "types": PRODUCT_TYPES, "metals": METAL_TYPES}


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
    return render(request, "import.html", user, stores=STORES)


@app.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request, file: UploadFile = File(...), store: str = Form(""), csrf: str = Form(""),
                        user=Depends(current_user)):
    check_csrf(request, csrf)
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return render(request, "import.html", user, stores=STORES, error="Файл больше 10 МБ.")
    try:
        plan = build_plan(data, file.filename or "", default_store=store or None)
    except ValueError as e:
        return render(request, "import.html", user, stores=STORES, error=str(e))
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
    return render(request, "labels.html", user, stores=STORES, articles=articles,
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
                                           store=form.get("store") or None)
    else:
        products = get_products_for_labels(store=form.get("store") or None)
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

@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, start: str = "", end: str = "", user=Depends(current_user)):
    d = today()
    start_d = parse_date(start) or d.replace(day=1)
    end_d = parse_date(end) or d
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    ops = get_period_operations(*day_bounds(start_d, end_d))
    if ops is None:
        raise HTTPException(500, "Ошибка базы данных.")
    return render(request, "reports.html", user, start=start_d, end=end_d, s=build_summary(ops), ops=ops,
                  payment_types=PAYMENT_TYPES)


@app.get("/reports.xlsx")
def reports_xlsx(start: str = "", end: str = "", user=Depends(current_user)):
    d = today()
    start_d, end_d = parse_date(start) or d, parse_date(end) or d
    ops = get_period_operations(*day_bounds(start_d, end_d))
    if ops is None:
        raise HTTPException(500, "Ошибка базы данных.")
    excel = build_excel(ops, build_summary(ops), start_d, end_d)
    return StreamingResponse(excel, media_type=XLSX,
                             headers={"Content-Disposition": f'attachment; filename="report_{start_d:%Y%m%d}_{end_d:%Y%m%d}.xlsx"'})


# ---------------------------------------------------------------- персонал

ROLE_BY_KEY = {'cashier': 'cashier', 'manager': 'manager', 'admin': 'admin'}


@app.get("/staff", response_class=HTMLResponse)
def staff(request: Request, user=Depends(admin_only)):
    return render(request, "staff.html", user, users=get_all_users() or [])


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
            ok, msg = add_user_to_db(phone, role, name, user.get('store_id'))
            flash(request, msg, "ok" if ok else "error")
    elif target == user['id'] and action in ("role", "deactivate"):
        flash(request, "Нельзя изменить собственную роль или деактивировать себя.", "error")
    elif action == "role" and target:
        ok, msg = change_user_role_function(target, ROLE_BY_KEY.get(form.get("role"), ""))
        flash(request, msg, "ok" if ok else "error")
    elif action == "deactivate" and target:
        ok, msg = deactivate_user_in_db(target)
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
