from telebot import types

# Create a function to generate the appropriate buttons for the cashier's functionality
def create_cashier_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_cash = types.KeyboardButton("Касса")
    keyboard.add(button_cash)
    return keyboard

# Create a function to generate the appropriate buttons for the admin's functionality
def create_admin_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2) # Adjusted row_width
    button_staff = types.KeyboardButton("Персонал")
    button_cash = types.KeyboardButton("Касса")
    button_stock = types.KeyboardButton("Склад") # New button
    keyboard.add(button_staff, button_cash, button_stock)
    return keyboard

# Create a function to generate the appropriate buttons for the manager's functionality
def create_manager_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    button_cash = types.KeyboardButton("Касса") # Managers might need cash ops
    button_stock = types.KeyboardButton("Склад")
    # Managers typically don't manage staff or main reports unless specified
    keyboard.add(button_cash, button_stock)
    return keyboard


def create_staff_management_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1) # Adjusted for fewer buttons
    button_add_user = types.KeyboardButton("Добавить пользователя")
    # button_remove_user = types.KeyboardButton("Удалить пользователя") # Removed
    button_rights = types.KeyboardButton("Права") # Deactivation is now part of "Права"
    button_back = types.KeyboardButton("Назад")
    keyboard.add(button_add_user, button_rights, button_back) # Removed "Удалить"
    return keyboard

def create_cash_operations_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    button_sale = types.KeyboardButton("Продажа")
    button_return = types.KeyboardButton("Возврат")
    button_trade_in = types.KeyboardButton("Trade-In")
    button_buyback = types.KeyboardButton("Скупка")
    button_reports = types.KeyboardButton("Отчеты")
    button_back = types.KeyboardButton("Назад")
    keyboard.add(button_sale, button_return, button_trade_in, button_buyback, button_reports, button_back)
    return keyboard

# New: Buttons for Stock Management menu
def create_stock_management_buttons():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1) # Single column for clarity
    btn_add_product = types.KeyboardButton("Добавить товар")
    btn_stock_report = types.KeyboardButton("Отчет об остатках")
    btn_restock_product = types.KeyboardButton("Пополнить остатки")
    btn_back_main = types.KeyboardButton("Назад") # To main menu
    keyboard.add(btn_add_product, btn_stock_report, btn_restock_product, btn_back_main)
    return keyboard


# Helper for simple "Отмена" keyboard during a flow
def create_cancel_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    keyboard.add(types.KeyboardButton("Отмена"))
    return keyboard

# Helper for "Подтвердить" / "Отмена"
def create_confirmation_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    btn_confirm = types.KeyboardButton("Подтвердить")
    btn_cancel = types.KeyboardButton("Отмена")
    keyboard.add(btn_confirm, btn_cancel)
    return keyboard

# Helper for payment type selection
def create_payment_type_markup(): # Generic one
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    btn_cash = types.KeyboardButton("Наличные")
    btn_card = types.KeyboardButton("Безналичные")
    btn_cancel = types.KeyboardButton("Отмена")
    keyboard.add(btn_cash, btn_card)
    keyboard.add(btn_cancel)
    return keyboard