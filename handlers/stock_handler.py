"""Склад: добавление товара, фото товара, пополнение остатков, отчёт об остатках."""
import logging
from decimal import Decimal

from telebot import types

from handlers.buttons import (create_cancel_markup, create_confirmation_markup, create_choice_markup, create_skip_cancel_markup,
                     create_stock_management_buttons, BTN_CONFIRM)
from core.database import add_product_to_db, get_stock_report_data, update_product_quantity, get_product_by_article, get_enum_or_distinct_values
from handlers.common import store_names, store_id_by_name, store_name_by_id, STOCK_ROLES, in_state, is_button, register_simple_cancel, product_line, SKIP
from services import photos
from core.schema import METAL_TYPES, PRODUCT_TYPES
from core.state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.stock_report import build_stock_excel, stock_totals
from utils.common import parse_float, parse_int, parse_money, money, h, now

logger = logging.getLogger(__name__)


def stock_menu(chat_id, text):
    bot.send_message(chat_id, text, reply_markup=create_stock_management_buttons())


def _choices(column, fallback):
    values = get_enum_or_distinct_values('products', column, add_other_option=False)
    return list(values) if values else list(fallback)


# ------------------------------------------------------------ добавление товара
# Шаги: (состояние, ключ, вопрос)
ADD_STEPS = [
    ("STOCK_ADD_ARTICLE", 'article_number', "Артикул нового товара:"),
    ("STOCK_ADD_STORE", 'store_id', "Магазин:"),
    ("STOCK_ADD_TYPE", 'product_type', "Тип изделия:"),
    ("STOCK_ADD_MODEL", 'model', "Модель / название:"),
    ("STOCK_ADD_METAL", 'metal_type', "Металл:"),
    ("STOCK_ADD_SIZE", 'size', "Размер (если есть):"),
    ("STOCK_ADD_FINENESS", 'fineness', "Проба (например, 585):"),
    ("STOCK_ADD_INSERTS", 'inserts', "Вставки / камни (например, фианит, бриллиант 0.1 ct):"),
    ("STOCK_ADD_WEIGHT", 'weight', "Вес в граммах (например, 2.53):"),
    ("STOCK_ADD_PPG", 'price_per_gram', "Цена за грамм (если не применяется — «Пропустить»):"),
    ("STOCK_ADD_PRICE", 'price', "Цена продажи:"),
    ("STOCK_ADD_MIN_PRICE", 'min_price', "Минимальная цена продажи (ниже неё продать нельзя):"),
    ("STOCK_ADD_QTY", 'quantity', "Количество на складе:"),
    ("STOCK_ADD_MANUFACTURER", 'manufacturer', "Производитель:"),
    ("STOCK_ADD_COMMENT", 'commentaries', "Комментарий:"),
    ("STOCK_ADD_TRACK", 'track_stock', "Присылать уведомление, когда остаток станет низким?"),
]
ADD_CONFIRM = "STOCK_ADD_CONFIRM"
STEP_INDEX = {state: i for i, (state, _, _) in enumerate(ADD_STEPS)}
FIELD_TITLES = {
    'article_number': 'Артикул', 'store_id': 'Магазин', 'product_type': 'Тип', 'model': 'Модель', 'metal_type': 'Металл',
    'size': 'Размер', 'fineness': 'Проба', 'inserts': 'Вставки', 'weight': 'Вес, г', 'price_per_gram': 'Цена за грамм', 'price': 'Цена', 'min_price': 'Мин. цена',
    'quantity': 'Количество', 'manufacturer': 'Производитель', 'commentaries': 'Комментарий', 'track_stock': 'Уведомлять об остатке',
}


def _ask_step(chat_id, index):
    state, key, question = ADD_STEPS[index]
    user_state[chat_id] = state
    op = get_op(chat_id)
    text = f"Шаг {index + 1}/{len(ADD_STEPS)}. {question}"
    if key == 'store_id':
        markup = create_choice_markup(store_names(), 2)
    elif key == 'product_type':
        markup = create_choice_markup(_choices('product_type', PRODUCT_TYPES), 3)
    elif key == 'metal_type':
        markup = create_choice_markup(_choices('metal_type', METAL_TYPES), 3)
    elif key in ('size', 'inserts', 'price_per_gram', 'manufacturer', 'commentaries'):
        markup = create_skip_cancel_markup(SKIP)
    elif key == 'fineness':
        markup = create_choice_markup(["585", "750", "925", SKIP], 4)
    elif key == 'min_price':
        markup = create_skip_cancel_markup(money(op['data']['price']))
    elif key == 'quantity':
        markup = create_choice_markup(["1", "2", "5"], 3)
    elif key == 'track_stock':
        markup = create_choice_markup(["Да", "Нет"], 2)
    else:
        markup = create_cancel_markup()
    bot.send_message(chat_id, text, reply_markup=markup)


def start_add(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, ADD_STEPS[0][0])
    get_op(chat_id)['data'] = {}
    _ask_step(chat_id, 0)


def _validate(chat_id, key, text):
    """Возвращает (ok, значение или текст ошибки)."""
    data = get_op(chat_id)['data']
    skip = text == SKIP
    if key == 'article_number':
        if not text or len(text) > 64:
            return False, "Артикул не может быть пустым или длиннее 64 символов."
        existing = get_product_by_article(text)
        if existing:
            return False, f"Товар с артикулом {text} уже есть ({existing.get('model')}). Введите другой артикул."
        return True, text
    if key == 'store_id':
        store_id = store_id_by_name(text)
        return (True, store_id) if store_id else (False, "Выберите магазин кнопкой.")
    if key in ('product_type', 'metal_type'):
        fallback = {'product_type': PRODUCT_TYPES, 'metal_type': METAL_TYPES}[key]
        options = _choices(key, fallback)
        return (True, text) if text in options else (False, "Выберите вариант кнопкой.")
    if key == 'model':
        return (True, text[:255]) if text else (False, "Модель не может быть пустой.")
    if key == 'size':
        return True, (None if skip else text[:32])
    if key == 'fineness':
        if skip:
            return True, None
        return (True, text[:16]) if len(text) <= 16 else (False, "Проба — короткое значение, например 585.")
    if key == 'inserts':
        return True, (None if skip else text[:255])
    if key == 'weight':
        w = parse_float(text)
        return (True, round(w, 3)) if w and 0 < w < 10000 else (False, "Вес — положительное число, например 2.53.")
    if key == 'price_per_gram':
        if skip:
            return True, Decimal(0)
        v = _money(text, allow_zero=True)
        return (True, v) if v is not None else (False, "Цена за грамм — неотрицательное число.")
    if key == 'price':
        v = _money(text)
        return (True, v) if v is not None else (False, "Цена — положительное число.")
    if key == 'min_price':
        v = _money(text, allow_zero=True)
        if v is None:
            return False, "Минимальная цена — неотрицательное число."
        if v > data['price']:
            return False, f"Минимальная цена не может быть выше цены продажи ({money(data['price'])})."
        return True, v
    if key == 'quantity':
        q = parse_int(text)
        return (True, q) if q is not None and 0 <= q <= 100000 else (False, "Количество — целое число от 0.")
    if key in ('manufacturer', 'commentaries'):
        return True, (None if skip else text[:500])
    if key == 'track_stock':
        return (True, 1 if text == "Да" else 0) if text in ("Да", "Нет") else (False, "Ответьте «Да» или «Нет».")
    return False, "Неизвестный шаг."


def _money(text, allow_zero=False):
    v = parse_money(text)
    if v is None or v < 0 or (v == 0 and not allow_zero):
        return None
    return v.quantize(1)


def on_add_step(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    index = STEP_INDEX[user_state[chat_id]]
    key = ADD_STEPS[index][1]
    ok, value = _validate(chat_id, key, text)
    if not ok:
        bot.send_message(chat_id, value)
        _ask_step(chat_id, index)
        return
    get_op(chat_id)['data'][key] = value
    if index + 1 < len(ADD_STEPS):
        _ask_step(chat_id, index + 1)
        return
    user_state[chat_id] = ADD_CONFIRM
    data = get_op(chat_id)['data']
    lines = ["<b>Проверьте новый товар</b>"]
    for _, k, _ in ADD_STEPS:
        v = data.get(k)
        if k in ('price', 'min_price', 'price_per_gram'):
            v = money(v)
        elif k == 'track_stock':
            v = "да" if v else "нет"
        elif k == 'store_id':
            v = store_name_by_id(v)
        lines.append(f"{FIELD_TITLES[k]}: {h(v if v not in (None, '') else '—')}")
    bot.send_message(chat_id, "\n".join(lines), parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_add_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    data = dict(get_op(chat_id)['data'])
    data['arrival_date'] = now().date()
    ok, msg = add_product_to_db(data)
    end_op(chat_id)
    created = get_product_by_article(data['article_number']) if ok else None
    if not created:
        stock_menu(chat_id, ("✅ " if ok else "❌ ") + msg)
        return
    _ask_photos(chat_id, created, f"✅ {msg}\n\n")


# ------------------------------------------------------------ фото товара (необязательно)
PHOTO_ARTICLE, PHOTO_WAIT = "STOCK_PHOTO_ARTICLE", "STOCK_PHOTO_WAIT"
BTN_DONE = "Готово"


def _ask_photos(chat_id, product, prefix=""):
    start_op(chat_id, PHOTO_WAIT)
    op = get_op(chat_id)
    op['product'], op['added'] = product, 0
    have = len(photos.for_product(product['id']) or [])
    note = f" У товара уже {have} фото, можно ещё {photos.MAX_PER_PRODUCT - have}." if have else " Первое станет главным."
    bot.send_message(chat_id, f"{prefix}📷 Пришлите фото {product_line(product)} — одно или несколько, или нажмите "
                              f"«{BTN_DONE}», если фото не нужно.{note}\nФото увидит продавец на кассе.",
                     parse_mode='HTML', reply_markup=create_choice_markup([BTN_DONE], 1))


def start_photo(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, PHOTO_ARTICLE)
    bot.send_message(chat_id, "Фото к товару. Введите артикул:", reply_markup=create_cancel_markup())


def on_photo_article(message):
    chat_id, article = message.chat.id, (message.text or '').strip()
    product = get_product_by_article(article)
    if not product:
        bot.send_message(chat_id, f"Артикул <code>{h(article)}</code> не найден. Введите другой.", parse_mode='HTML',
                         reply_markup=create_cancel_markup())
        return
    _ask_photos(chat_id, product)


def on_photo(message):
    chat_id = message.chat.id
    op = get_op(chat_id)
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
    elif message.content_type == 'document' and (message.document.mime_type or '').startswith('image/'):
        if (message.document.file_size or 0) > photos.MAX_BYTES:
            bot.send_message(chat_id, "Файл больше 10 МБ — пришлите фото поменьше.")
            return
        file_id = message.document.file_id
    else:
        bot.send_message(chat_id, f"Пришлите фото или нажмите «{BTN_DONE}».",
                         reply_markup=create_choice_markup([BTN_DONE], 1))
        return
    try:
        data = bot.download_file(bot.get_file(file_id).file_path)
    except Exception as e:  # noqa: BLE001 — сеть Telegram: сообщаем и ждём следующее фото
        logger.warning("Chat %s: не удалось скачать фото: %s", chat_id, e)
        bot.send_message(chat_id, "Не удалось получить фото из Telegram. Пришлите его ещё раз.")
        return
    ok, result = photos.add(op['product']['id'], data, _user_id(chat_id))
    if not ok:
        bot.send_message(chat_id, f"❌ {result}", reply_markup=create_choice_markup([BTN_DONE], 1))
        return
    op['added'] = op.get('added', 0) + 1
    bot.send_message(chat_id, f"📷 Фото сохранено ({op['added']}). Пришлите ещё или нажмите «{BTN_DONE}».",
                     reply_markup=create_choice_markup([BTN_DONE], 1))


def on_photo_text(message):
    chat_id = message.chat.id
    if (message.text or '').strip() not in (BTN_DONE, SKIP):
        bot.send_message(chat_id, f"Пришлите фото или нажмите «{BTN_DONE}».", reply_markup=create_choice_markup([BTN_DONE], 1))
        return
    added = get_op(chat_id).get('added', 0)
    end_op(chat_id)
    stock_menu(chat_id, f"Готово, добавлено фото: {added}." if added else "Готово, без фото.")


def _user_id(chat_id):
    from core.database import get_user_by_chat_id
    user = get_user_by_chat_id(chat_id)
    return user['id'] if user else None


# ------------------------------------------------------------ пополнение
RESTOCK_ARTICLE, RESTOCK_QTY, RESTOCK_CONFIRM = "STOCK_RESTOCK_ARTICLE", "STOCK_RESTOCK_QTY", "STOCK_RESTOCK_CONFIRM"


def start_restock(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, RESTOCK_ARTICLE)
    bot.send_message(chat_id, "Пополнение остатков. Введите артикул товара:", reply_markup=create_cancel_markup())


def on_restock_article(message):
    chat_id, article = message.chat.id, (message.text or '').strip()
    product = get_product_by_article(article)
    if not product:
        bot.send_message(chat_id, f"Артикул <code>{h(article)}</code> не найден. Введите другой.", parse_mode='HTML',
                         reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['product'] = product
    user_state[chat_id] = RESTOCK_QTY
    bot.send_message(chat_id, f"{product_line(product)}\nСейчас на складе: <b>{product['quantity']}</b> шт.\n\nСколько добавить?",
                     parse_mode='HTML', reply_markup=create_choice_markup(["1", "2", "5"], 3))


def on_restock_qty(message):
    chat_id = message.chat.id
    qty = parse_int(message.text)
    if qty is None or qty <= 0 or qty > 100000:
        bot.send_message(chat_id, "Введите целое положительное число.", reply_markup=create_choice_markup(["1", "2", "5"], 3))
        return
    op = get_op(chat_id)
    op['qty'] = qty
    user_state[chat_id] = RESTOCK_CONFIRM
    p = op['product']
    bot.send_message(chat_id, f"Добавить {qty} шт. к {product_line(p)}?\nБудет: {p['quantity'] + qty} шт.",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_restock_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    ok, msg = update_product_quantity(op['product']['article_number'], op['qty'])
    end_op(chat_id)
    stock_menu(chat_id, ("✅ " if ok else "❌ ") + msg)


# ------------------------------------------------------------ отчёт об остатках

def stock_report(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    items = get_stock_report_data()
    if items is None:
        stock_menu(chat_id, "❌ Ошибка базы данных, отчёт не сформирован.")
        return
    if not items:
        stock_menu(chat_id, "Товаров на складе пока нет.")
        return
    buffer = build_stock_excel(items)
    lines = ["<b>Остатки на складе</b>"]
    for store, (positions, qty, value) in stock_totals(items).items():
        lines.append(f"{h(store)}: в наличии позиций — {positions}, всего {qty} шт. на {money(value)}")
    bot.send_message(chat_id, "\n".join(lines), parse_mode='HTML')
    bot.send_document(chat_id, types.InputFile(buffer, file_name=f"stock_{now():%Y%m%d_%H%M}.xlsx"))
    stock_menu(chat_id, "Выберите следующее действие:")


def register_stock_handlers():
    register_simple_cancel("STOCK_", "Операция со складом", menu=stock_menu)
    bot.register_message_handler(start_add, func=is_button("Добавить товар", *STOCK_ROLES))
    bot.register_message_handler(start_restock, func=is_button("Пополнить остатки", *STOCK_ROLES))
    bot.register_message_handler(stock_report, func=is_button("Отчет об остатках", *STOCK_ROLES))
    bot.register_message_handler(on_add_step, func=lambda m: user_state.get(m.chat.id) in STEP_INDEX)
    bot.register_message_handler(on_add_confirm, func=in_state(ADD_CONFIRM))
    bot.register_message_handler(on_restock_article, func=in_state(RESTOCK_ARTICLE))
    bot.register_message_handler(on_restock_qty, func=in_state(RESTOCK_QTY))
    bot.register_message_handler(on_restock_confirm, func=in_state(RESTOCK_CONFIRM))
    bot.register_message_handler(start_photo, func=is_button("Фото к товару", *STOCK_ROLES))
    bot.register_message_handler(on_photo_article, func=in_state(PHOTO_ARTICLE))
    bot.register_message_handler(on_photo, content_types=['photo', 'document'], func=in_state(PHOTO_WAIT))
    bot.register_message_handler(on_photo_text, func=in_state(PHOTO_WAIT))
