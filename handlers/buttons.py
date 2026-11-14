from telebot import types

from core import config
from core.state import user_data

PAYMENT_TYPES = ["Наличные", "Безналичные", "Kaspi"]
ROLE_TITLES = {'admin': 'администратор', 'manager': 'менеджер', 'cashier': 'кассир'}

# Кнопки меню — используются и как фильтры хендлеров
BTN_CASH, BTN_STOCK, BTN_STAFF, BTN_BACK, BTN_CANCEL = "Касса", "Склад", "Персонал", "Назад", "Отмена"
BTN_CONFIRM = "Подтвердить"
BTN_WEB = "Веб-админка"
MENU_BUTTONS = {BTN_CASH, BTN_STOCK, BTN_STAFF, BTN_BACK}


def _kb(*rows, one_time=False):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=one_time)
    for row in rows:
        keyboard.row(*[types.KeyboardButton(text) for text in row])
    return keyboard


def create_cashier_buttons():
    return _kb([BTN_CASH])


def create_manager_buttons():
    return _kb([BTN_CASH, BTN_STOCK], *([[BTN_WEB]] if config.WEB_URL else []))


def create_admin_buttons():
    return _kb([BTN_CASH, BTN_STOCK], [BTN_STAFF] + ([BTN_WEB] if config.WEB_URL else []))


def create_share_contact_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(types.KeyboardButton("Поделиться номером", request_contact=True))
    return keyboard


def main_menu_for_role(role):
    return {
        'admin': create_admin_buttons,
        'manager': create_manager_buttons,
        'cashier': create_cashier_buttons,
    }.get(role, create_share_contact_markup)()


def get_keyboard_by_role(chat_id):
    """Главное меню для пользователя чата (или кнопка авторизации)."""
    user = user_data.get(chat_id) or {}
    if not user.get('is_active') or user.get('role') not in ROLE_TITLES:
        return create_share_contact_markup()
    return main_menu_for_role(user['role'])


def create_staff_management_buttons():
    return _kb(["Добавить пользователя", "Права"], [BTN_BACK])


def create_cash_operations_buttons():
    return _kb(["Продажа", "Возврат"], ["Trade-In", "Скупка"], ["Инкассация", "Отчеты"], [BTN_BACK])


def create_stock_management_buttons():
    return _kb(["Добавить товар", "Пополнить остатки"], ["Фото к товару", "Ценники"], ["Импорт каталога", "Отчет об остатках"],
               [BTN_BACK])


def create_cancel_markup():
    return _kb([BTN_CANCEL])


def create_skip_cancel_markup(skip_text="Пропустить"):
    return _kb([skip_text], [BTN_CANCEL])


def create_confirmation_markup():
    return _kb([BTN_CONFIRM, BTN_CANCEL])


def create_payment_type_markup():
    return _kb(PAYMENT_TYPES, [BTN_CANCEL])


def create_choice_markup(options, row_width=2):
    rows = [options[i:i + row_width] for i in range(0, len(options), row_width)]
    return _kb(*rows, [BTN_CANCEL])
