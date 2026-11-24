"""Общие помощники для многошаговых операций."""
import logging

from core.auth import has_role
from handlers.buttons import (create_cash_operations_buttons, create_choice_markup, create_skip_cancel_markup,
                     create_cancel_markup, BTN_CANCEL)
from core.database import (get_product_by_article, get_stores, find_by_mark_code, marking_required, SELLABLE_CODE,
                      CODE_STATUS_TITLES, foreign_store_error)
from utils import mark_codes
from core.schema import METAL_TYPES
from core.state import bot, user_data, user_state, end_op, get_op, register_cancel
from utils.common import parse_float, parse_money, money, h

logger = logging.getLogger(__name__)

CASH_ROLES = ('admin', 'manager', 'cashier')
STOCK_ROLES = ('admin', 'manager')
NO_ARTICLE = "Без артикула"
SKIP = "Пропустить"


def store_names():
    return [st['name'] for st in (get_stores() or [])]


def store_id_by_name(name):
    for st in get_stores() or []:
        if st['name'] == name:
            return st['id']
    return None


def store_name_by_id(store_id):
    for st in get_stores(active_only=False) or []:
        if st['id'] == store_id:
            return st['name']
    return None


def in_state(state):
    return lambda m: user_state.get(m.chat.id) == state


def is_button(text, *roles):
    return lambda m: m.text == text and has_role(m.chat.id, *roles)


def user_ids(chat_id):
    """(id сотрудника, магазин операции): выбранный в операции или магазин сотрудника."""
    session = user_data.get(chat_id) or {}
    return session.get('id'), session.get('op', {}).get('store_id') or session.get('store_id')


def needs_store_choice(chat_id):
    """Сотрудник не привязан к магазину, а магазинов несколько — магазин операции нужно спросить."""
    session = user_data.get(chat_id) or {}
    if session.get('store_id'):
        return False
    stores = get_stores() or []
    if len(stores) == 1:
        session.setdefault('op', {})['store_id'] = stores[0]['id']
        return False
    return len(stores) > 1


def ask_store(chat_id, state, text="В каком магазине операция?"):
    user_state[chat_id] = state
    bot.send_message(chat_id, text, reply_markup=create_choice_markup(store_names(), 2))


def read_store(message):
    """id магазина из ответа или None (и повторный вопрос)."""
    store_id = store_id_by_name((message.text or '').strip())
    if store_id:
        get_op(message.chat.id)['store_id'] = store_id
    else:
        bot.send_message(message.chat.id, "Выберите магазин кнопкой.", reply_markup=create_choice_markup(store_names(), 2))
    return store_id


def cash_menu(chat_id, text):
    bot.send_message(chat_id, text, reply_markup=create_cash_operations_buttons())


def register_simple_cancel(prefix, title, menu=cash_menu):
    def cancel(chat_id, notify):
        end_op(chat_id)
        if notify:
            menu(chat_id, f"{title} отменена.")
    register_cancel(prefix, cancel)


def product_line(product):
    parts = [h(product.get('model') or product.get('product_type') or '')]
    metal = product.get('metal_type')
    if metal and product.get('fineness'):
        metal = f"{metal} {product['fineness']}"
    details = [product.get('product_type'), metal]
    if product.get('weight'):
        details.append(f"{product['weight']} г")
    if product.get('size'):
        details.append(f"р. {product['size']}")
    details = [h(d) for d in details if d]
    if details:
        parts.append("(" + ", ".join(details) + ")")
    parts.append(f"арт. <code>{h(product.get('article_number'))}</code>")
    return " ".join(parts)


class CustomerItemIntake:
    """Приём изделия от клиента (для скупки и trade-in).

    Изделие либо из нашего ассортимента (по артикулу, вернётся на склад),
    либо стороннее — тогда спрашиваем металл, вес и описание.
    Результат кладётся в op['item'] и вызывается on_done(chat_id).
    """

    def __init__(self, prefix, on_done):
        self.prefix = prefix
        self.on_done = on_done
        self.ARTICLE = f"{prefix}_ITEM_ARTICLE"
        self.METAL = f"{prefix}_ITEM_METAL"
        self.WEIGHT = f"{prefix}_ITEM_WEIGHT"
        self.DESC = f"{prefix}_ITEM_DESC"

    def start(self, chat_id, intro):
        user_state[chat_id] = self.ARTICLE
        bot.send_message(chat_id, f"{intro}\nВведите артикул, если изделие из нашего магазина, или нажмите «{NO_ARTICLE}».",
                         reply_markup=create_choice_markup([NO_ARTICLE], 1))

    def register(self):
        bot.register_message_handler(self._article, func=in_state(self.ARTICLE))
        bot.register_message_handler(self._metal, func=in_state(self.METAL))
        bot.register_message_handler(self._weight, func=in_state(self.WEIGHT))
        bot.register_message_handler(self._desc, func=in_state(self.DESC))

    def _article(self, message):
        chat_id, text = message.chat.id, (message.text or '').strip()
        if text == NO_ARTICLE:
            user_state[chat_id] = self.METAL
            bot.send_message(chat_id, "Металл изделия:", reply_markup=create_choice_markup(list(METAL_TYPES), 3))
            return
        product = get_product_by_article(text)
        if not product:
            bot.send_message(chat_id, f"Артикул <code>{h(text)}</code> не найден. Введите другой или нажмите «{NO_ARTICLE}».",
                             parse_mode='HTML', reply_markup=create_choice_markup([NO_ARTICLE], 1))
            return
        get_op(chat_id)['item'] = {
            'article': product['article_number'],
            'description': product.get('model'),
            'metal_type': product.get('metal_type'),
            'weight': product.get('weight'),
            'title': product_line(product),
            'list_price': product.get('price'),
        }
        self.on_done(chat_id)

    def _metal(self, message):
        chat_id, text = message.chat.id, (message.text or '').strip()
        if text not in METAL_TYPES:
            bot.send_message(chat_id, "Выберите металл кнопкой.", reply_markup=create_choice_markup(list(METAL_TYPES), 3))
            return
        get_op(chat_id)['item'] = {'article': None, 'metal_type': text}
        user_state[chat_id] = self.WEIGHT
        bot.send_message(chat_id, "Вес изделия в граммах (например, 3.45):", reply_markup=create_cancel_markup())

    def _weight(self, message):
        chat_id = message.chat.id
        weight = parse_float(message.text)
        if weight is None or weight <= 0 or weight > 10000:
            bot.send_message(chat_id, "Вес должен быть положительным числом, например 3.45.", reply_markup=create_cancel_markup())
            return
        get_op(chat_id)['item']['weight'] = round(weight, 3)
        user_state[chat_id] = self.DESC
        bot.send_message(chat_id, "Короткое описание изделия (тип, проба, особенности):", reply_markup=create_skip_cancel_markup(SKIP))

    def _desc(self, message):
        chat_id, text = message.chat.id, (message.text or '').strip()
        item = get_op(chat_id)['item']
        item['description'] = None if text == SKIP else text[:500]
        title = f"{item['metal_type']}, {item['weight']} г"
        if item['description']:
            title = f"{item['description']} ({title})"
        item['title'] = h(title) + ", без артикула"
        self.on_done(chat_id)


def resolve_for_sale(chat_id, text):
    """Артикул или код маркировки товара для продажи → (товар, код | None). При ошибке пишет в чат и возвращает (None, None)."""
    text = (text or '').strip()
    if mark_codes.looks_like_code(text):
        found = find_by_mark_code(text)
        if found:
            if found['code']['status'] not in SELLABLE_CODE:
                bot.send_message(chat_id, f"Код {h(mark_codes.short(text))} уже {CODE_STATUS_TITLES[found['code']['status']]}. "
                                          "Отсканируйте другой.", reply_markup=create_cancel_markup())
                return None, None
            return found['product'], text
    product = get_product_by_article(text)
    if not product:
        what = "Код маркировки" if mark_codes.looks_like_code(text) else "Артикул"
        bot.send_message(chat_id, f"{what} <code>{h(mark_codes.short(text))}</code> не найден. Введите другой или нажмите «Отмена».",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return None, None
    store_error = foreign_store_error(user_data.get(chat_id), product.get('store_id'), product.get('Store'))
    if store_error:
        bot.send_message(chat_id, f"{product_line(product)}\n{h(store_error)} Введите другой артикул или нажмите «Отмена».",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return None, None
    if marking_required(product):
        bot.send_message(chat_id, f"{product_line(product)} — маркируемый товар. Пришлите <b>код маркировки</b> с бирки: "
                                  "отсканируйте DataMatrix приложением-сканером и вставьте текст сюда "
                                  "(или продайте на кассе ПК со сканером).",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return None, None
    return product, None


def ask_money(chat_id, text, suggestion=None):
    markup = create_skip_cancel_markup(money(suggestion)) if suggestion else create_cancel_markup()
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')


def read_money(message, allow_negative=False, allow_zero=False):
    value = parse_money(message.text)
    if value is None:
        return None
    if value < 0 and not allow_negative:
        return None
    if value == 0 and not allow_zero:
        return None
    return value.quantize(1)


__all__ = ['BTN_CANCEL']
