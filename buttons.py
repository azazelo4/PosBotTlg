from telebot import types

# Create a function to generate the appropriate buttons for the cashier's functionality
def create_cashier_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_cash = types.KeyboardButton("Касса")
    # button_cancel = types.KeyboardButton("Отмена") # "Отмена" is context-dependent, better handled by specific flows or a global cancel
    keyboard.add(button_cash) # Removed general cancel from main menu
    return keyboard

# Create a function to generate the appropriate buttons for the admin's functionality
def create_admin_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_staff = types.KeyboardButton("Персонал")
    button_cash = types.KeyboardButton("Касса")
    # button_cancel = types.KeyboardButton("Отмена")
    keyboard.add(button_staff, button_cash) # Removed general cancel
    return keyboard


def create_staff_management_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    button_add_user = types.KeyboardButton("Добавить пользователя")
    button_remove_user = types.KeyboardButton("Удалить пользователя")
    button_rights = types.KeyboardButton("Права")
    button_back = types.KeyboardButton("Назад")
    # button_cancel = types.KeyboardButton("Отмена") # "Отмена" can be part of "Назад" or specific flow
    keyboard.add(button_add_user, button_remove_user, button_rights, button_back)
    return keyboard

def create_cash_operations_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    button_sale = types.KeyboardButton("Продажа")
    button_return = types.KeyboardButton("Возврат")
    button_trade_in = types.KeyboardButton("Trade-In")
    button_buyback = types.KeyboardButton("Скупка")
    button_reports = types.KeyboardButton("Отчеты")
    button_back = types.KeyboardButton("Назад") # To go back from cash operations to main menu
    # button_cancel = types.KeyboardButton("Отмена") # Handled by "Назад" or specific flow cancel
    keyboard.add(button_sale, button_return, button_trade_in, button_buyback, button_reports, button_back)
    return keyboard

# Helper for simple "Отмена" keyboard during a flow
def create_cancel_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(types.KeyboardButton("Отмена"))
    return keyboard

# Helper for "Подтвердить" / "Отмена"
def create_confirmation_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    # Adding buttons one by one to ensure they are on the same row if possible or distinct rows
    btn_confirm = types.KeyboardButton("Подтвердить")
    btn_cancel = types.KeyboardButton("Отмена")
    keyboard.add(btn_confirm, btn_cancel)
    return keyboard

# Helper for payment type selection
def create_payment_type_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    btn_cash = types.KeyboardButton("Наличные")
    btn_card = types.KeyboardButton("Безналичные")
    btn_cancel = types.KeyboardButton("Отмена")
    keyboard.add(btn_cash, btn_card)
    keyboard.add(btn_cancel)
    return keyboard