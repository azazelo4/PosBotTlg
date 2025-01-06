# user_management.py
from telebot import types
from state import bot, user_data, user_state
import logging

logger = logging.getLogger(__name__)

get_all_users_function = None
change_user_role_function = None

def configure_user_management(get_all_users_func, change_role_func):
    global get_all_users_function, change_user_role_function
    get_all_users_function = get_all_users_func
    change_user_role_function = change_role_func
    logger.debug("User management configured with external functions")

def handle_role_management(chat_id):
    logger.debug(f"Handling role management for chat_id: {chat_id}")
    user_state[chat_id] = 'USER_MANAGEMENT'

    user = user_data.get(chat_id)
    if not user or user.get('role') != 'admin':
        logger.warning(f"Unauthorized access attempt by chat_id: {chat_id}")
        bot.send_message(chat_id, "У вас недостаточно прав для управления ролями.")
        user_state[chat_id] = None
        return

    try:
        users = get_all_users_function()
        if not users:
            logger.info("No users found in the system")
            bot.send_message(chat_id, "Пользователи не найдены.")
            user_state[chat_id] = None
            return

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for user in users:
            button_text = f"{user['name']} ({user['role']})"
            callback_data = f"select_user_{user['id']}"
            keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
        
        # Add back button
        keyboard.add(types.InlineKeyboardButton(text="Назад", callback_data="back_to_main"))
        
        bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)
        logger.debug(f"Displayed user list for chat_id: {chat_id}")

    except Exception as e:
        logger.exception(f"Error in handle_role_management: {str(e)}")
        bot.send_message(chat_id, f"Произошла ошибка при получении списка пользователей")
        user_state[chat_id] = None

def handle_select_user(call):
    chat_id = call.message.chat.id
    logger.debug(f"Handling user selection for chat_id: {chat_id}")

    if call.data == "back_to_main":
        user_state[chat_id] = None
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        bot.send_message(chat_id, "Возвращаемся в главное меню")
        return

    try:
        user_id = call.data.split("_")[-1]
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        roles = ['admin', 'cashier']
        for role in roles:
            keyboard.add(types.InlineKeyboardButton(
                text=role.capitalize(),
                callback_data=f"change_role_{user_id}_{role}"
            ))
        keyboard.add(types.InlineKeyboardButton(text="Отмена", callback_data="back_to_main"))
        
        bot.edit_message_text(
            "Выберите новую роль:",
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=keyboard
        )
        logger.debug(f"Displayed role selection for user_id: {user_id}")

    except Exception as e:
        logger.exception(f"Error in handle_select_user: {str(e)}")
        bot.answer_callback_query(call.id, "Произошла ошибка")

def handle_change_role(call):
    chat_id = call.message.chat.id
    try:
        if call.data == "back_to_main":
            user_state[chat_id] = None
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
            bot.send_message(chat_id, "Операция отменена")
            return

        parts = call.data.split("_")
        user_id = parts[2]
        new_role = parts[3]
        
        logger.debug(f"Changing role for user_id: {user_id} to {new_role}")
        change_user_role_function(user_id, new_role)
        
        bot.edit_message_text(
            f"Роль пользователя успешно изменена на {new_role}",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
        user_state[chat_id] = None
        
    except Exception as e:
        logger.exception(f"Error in handle_change_role: {str(e)}")
        bot.answer_callback_query(call.id, "Произошла ошибка при изменении роли")
        user_state[chat_id] = None