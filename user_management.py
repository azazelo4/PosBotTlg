# user_management.py
from telebot import types
from state import bot, user_data

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
    try:
        users = get_all_users_function()
        if not users:
            bot.send_message(chat_id, "Пользователи не найдены.")
            return
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка при получении пользователей: {str(e)}")
        return

    # Создаем клавиатуру с пользователями
    keyboard = types.InlineKeyboardMarkup()
    for user in users:
        keyboard.add(types.InlineKeyboardButton(
            text=f"{user['name']} ({user['role']})",
            callback_data=f"select_user_{user['id']}"
        ))

    bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)

def handle_select_user(call):
    user_id = call.data.split("_")[-1]
    keyboard = types.InlineKeyboardMarkup()
    roles = ['admin', 'cashier']
    for role in roles:
        keyboard.add(types.InlineKeyboardButton(
            text=role.capitalize(),
            callback_data=f"change_role_{user_id}_{role}"
        ))
    bot.send_message(call.message.chat.id, "Выберите новую роль:", reply_markup=keyboard)

def handle_change_role(call):
    parts = call.data.split("_")
    user_id = parts[2]
    new_role = parts[3]
    try:
        change_user_role_function(user_id, new_role)
        bot.send_message(call.message.chat.id, "Роль пользователя обновлена.")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Ошибка при изменении роли: {str(e)}")
