from telebot import types

# Create a function to generate the appropriate buttons for the cashier's functionality
def create_cashier_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
#    button_sales = types.KeyboardButton("Продажа")
#    button_refunds = types.KeyboardButton("Возврат")
#    button_trade_in = types.KeyboardButton("Trade-In")
#    button_purchases = types.KeyboardButton("Скупка")
    button_cash = types.KeyboardButton("Касса")
    button_cancel = types.KeyboardButton("Отмена")
    keyboard.add(button_cancel, button_cash)
    return keyboard

# Create a function to generate the appropriate buttons for the admin's functionality
def create_admin_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
#    button_sales = types.KeyboardButton("Продажа")
#    button_refunds = types.KeyboardButton("Возврат")
#    button_trade_in = types.KeyboardButton("Trade-In")
#    button_purchases = types.KeyboardButton("Скупка")
#    button_product_cards = types.KeyboardButton("Добавить товар")
#    button_reports = types.KeyboardButton("Отчеты")
    button_staff = types.KeyboardButton("Персонал")
    button_cash = types.KeyboardButton("Касса")
#    button_access_rights = types.KeyboardButton("Права")
#    button_add_user = types.KeyboardButton("Добавить пользователя")
#    button_remove_user = types.KeyboardButton("Удалить пользователя")
#    button_inventory_count = types.KeyboardButton("Ревизия")
    button_cancel = types.KeyboardButton("Отмена")
#    keyboard.add(button_sales, button_refunds, button_trade_in, button_purchases)
#    keyboard.add(button_product_cards, button_reports,  button_inventory_count, button_cancel, button_staff)
    keyboard.add(button_staff, button_cash, button_cancel)
    return keyboard


def create_staff_management_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_add_user = types.KeyboardButton("Добавить пользователя")
    button_remove_user = types.KeyboardButton("Удалить пользователя")
    button_rights = types.KeyboardButton("Права")
    button_back = types.KeyboardButton("Назад")
    # Кнопки добавления, удаления пользователя, управления правами и возврата
    keyboard.add(button_add_user, button_remove_user, button_rights, button_back)
    return keyboard

def create_cash_operations_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_sale = types.KeyboardButton("Продажа")
    button_return = types.KeyboardButton("Возврат")
    button_trade_in = types.KeyboardButton("Trade-In")
    button_buyback = types.KeyboardButton("Скупка")
    button_reports = types.KeyboardButton("Отчеты")
    button_back = types.KeyboardButton("Назад")
    keyboard.add(button_sale, button_return, button_trade_in, button_buyback, button_reports, button_back)
    return keyboard
