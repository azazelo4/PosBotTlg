# user_management.py
from telebot import types
from state import bot, user_data, user_state
import logging

logger = logging.getLogger(__name__)
user_management = {}

get_all_users_function = None
change_user_role_function = None

def configure_user_management(bot_instance, global_user_data, get_users_func, change_role_func):
    global bot, get_all_users_function, change_user_role_function
    bot = bot_instance
    get_all_users_function = get_users_func
    change_user_role_function = change_role_func

    @bot.message_handler(func=lambda m: user_management.get(m.chat.id) is None and m.text == 'Персонал')
    def start_management(message):
        chat_id = message.chat.id
        if chat_id in global_user_data:
            user_management[chat_id] = UserManagement(global_user_data[chat_id], user_state)
            bot.send_message(chat_id, "выберите действие:")
        else:
            bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, поделитесь своим номером телефона для авторизации, введя команду /start.")

class UserManagement:
    def __init__(self, user_data, user_state):
        self.state = None
        self.users = []
        self.user_roles = {}
        self.selected_user = None

    def handle_role_management(self, chat_id, user_data, user_state):
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

@bot.message_handler(func=lambda message: user_state.get(message.chat.id) == 'ADDING_USER')
def process_add_user_data(message):
    chat_id = message.chat.id
    try:
        data = message.text.split(", ")
        if len(data) != 2:
            bot.send_message(chat_id, "Неверный формат данных. Введите в формате: Имя, Телефон.")
            return
        name, phone = data
        response = add_user_to_db(phone, "cashier", name)
        bot.send_message(chat_id, response)
        user_state[chat_id] = None
    except Exception as e:
        logger.exception("Error adding user.")
        bot.send_message(chat_id, "Произошла ошибка. Попробуйте снова.")


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

@bot.message_handler(func=lambda message: message.text == "Добавить пользователя")
def add_user_handler(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Введите данные нового пользователя (например: Имя, Телефон):")
    user_state[chat_id] = 'ADDING_USER'
    return

@bot.message_handler(func=lambda message: message.text == "Удалить пользователя")
def delete_user_handler(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Введите идентификатор пользователя, которого вы хотите удалить:")
    user_state[chat_id] = 'DELETING_USER'
    return

@bot.message_handler(func=lambda message: message.text == "Права")
def role_management_handler(message):
    chat_id = message.chat.id
    user_management_instance = user_management.get(chat_id)
    if user_management_instance:
        user_management_instance.handle_role_management(chat_id, user_data, user_state)
    else:
        bot.send_message(chat_id, "Ошибка: Не удалось инициализировать управление ролями.")


# At the bottom of your script or in the bot initialization code
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_user_"))
def handle_select_user_callback(call):
    handle_select_user(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("change_role_") or call.data == "back_to_main")
def handle_change_role_callback(call):
    handle_change_role(call)
