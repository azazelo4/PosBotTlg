from telebot import types
from database import add_user_to_db, get_all_users, change_user_role_function
import logging

logger = logging.getLogger(__name__)

class UserManagement:
    def __init__(self, bot_instance, global_user_data, get_users_func, change_role_func):
        self.bot = bot_instance
        self.user_data = global_user_data
        self.user_state = {}
        self.get_all_users_function = get_users_func
        self.change_user_role_function = change_role_func
        self.logger = logging.getLogger(__name__)

    def configure_handlers(self):
        @self.bot.message_handler(func=lambda m: m.text == 'Персонал')
        def start_management(message):
            chat_id = message.chat.id
            if chat_id in self.user_data and self.user_data[chat_id].get('role') == 'admin':
                self.handle_role_management(chat_id)
            else:
                self.bot.send_message(chat_id, "У вас недостаточно прав для этой операции.")

    def handle_role_management(self, chat_id):
        self.logger.debug(f"Handling role management for chat_id: {chat_id}")
        self.user_state[chat_id] = 'USER_MANAGEMENT'

        try:
            users = self.get_all_users_function()
            if not users:
                self.bot.send_message(chat_id, "Пользователи не найдены.")
                return

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            for user in users:
                button_text = f"{user['name']} ({user['role']})"
                callback_data = f"select_user_{user['id']}"
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))

            keyboard.add(types.InlineKeyboardButton(text="Назад", callback_data="back_to_main"))
            self.bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)

        except Exception as e:
            self.logger.exception(f"Error in handle_role_management: {str(e)}")
            self.bot.send_message(chat_id, "Произошла ошибка при получении списка пользователей")

    def handle_select_user(self, call):
        chat_id = call.message.chat.id
        self.logger.debug(f"Handling user selection for chat_id: {chat_id}")

        if call.data == "back_to_main":
            self.user_state[chat_id] = None
            self.bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
            self.bot.send_message(chat_id, "Возвращаемся в главное меню")
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
            
            self.bot.edit_message_text(
                "Выберите новую роль:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=keyboard
            )

        except Exception as e:
            self.logger.exception(f"Error in handle_select_user: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка")

    def handle_change_role(self, call):
        chat_id = call.message.chat.id
        try:
            if call.data == "back_to_main":
                self.user_state[chat_id] = None
                self.bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
                self.bot.send_message(chat_id, "Операция отменена")
                return

            parts = call.data.split("_")
            user_id = parts[2]
            new_role = parts[3]
            
            self.change_user_role_function(user_id, new_role)
            self.bot.edit_message_text(
                f"Роль пользователя успешно изменена на {new_role}",
                chat_id=chat_id,
                message_id=call.message.message_id
            )
            
        except Exception as e:
            self.logger.exception(f"Error in handle_change_role: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка при изменении роли")
        finally:
            self.user_state[chat_id] = None

    def register_callback_handlers(self):
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("select_user_"))
        def callback_select_user(call):
            self.handle_select_user(call)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("change_role_") or call.data == "back_to_main")
        def callback_change_role(call):
            self.handle_change_role(call)

def configure_user_management(bot_instance, global_user_data, get_users_func, change_role_func):
    user_management_instance = UserManagement(bot_instance, global_user_data, get_users_func, change_role_func)
    user_management_instance.configure_handlers()
    user_management_instance.register_callback_handlers()

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
