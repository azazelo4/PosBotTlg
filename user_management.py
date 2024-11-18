# user_management.py
from telebot import types

get_all_users_function = None
change_user_role_function = None

def configure_user_management(get_all_users_func, change_role_func):
    global get_all_users_function, change_user_role_function
    get_all_users_function = get_all_users_func
    change_user_role_function = change_role_func

def handle_role_management(chat_id):
    """
    Обработчик команды 'Права'.
    Показывает список пользователей для управления их ролями.
    """
    # Проверяем, что пользователь администратор
    user = user_data.get(chat_id)
    if not user or user.get('role') != 'admin':
        bot.send_message(chat_id, "У вас недостаточно прав для управления ролями.")
        return

    # Получаем список пользователей
    users = get_all_users_function()
    if not users:
        bot.send_message(chat_id, "Пользователи не найдены.")
        return

    # Создаем клавиатуру с пользователями
    keyboard = types.InlineKeyboardMarkup()
    for user in users:
        keyboard.add(types.InlineKeyboardButton(
            text=f"{user['name']} ({user['role']})",
            callback_data=f"select_user_{user['id']}"
        ))

    bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)

# Остальные функции аналогично
