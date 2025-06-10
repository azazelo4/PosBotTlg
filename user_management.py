from telebot import types
from database import add_user_to_db, deactivate_user_in_db # Added deactivate_user_in_db
import logging
from state import user_state as global_main_user_state # Use the main user_state for FSM

logger = logging.getLogger(__name__)

# Define states for user management that will be stored in global_main_user_state
UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
UM_STATE_AWAITING_DELETE_USER_PHONE = "UM_AWAITING_DELETE_USER_PHONE"
# USER_MANAGEMENT state (for inline keyboard) is already handled by global_main_user_state in main.py for "Права"

class UserManagement:
    def __init__(self, bot_instance, global_user_data_ref, get_users_func, change_role_func):
        self.bot = bot_instance
        self.global_user_data = global_user_data_ref # Main user_data from state.py
        # self.user_state = {} # No longer using internal user_state; will use global_main_user_state
        self.get_all_users_function = get_users_func
        self.change_user_role_function = change_role_func
        self.logger = logging.getLogger(__name__)

    def configure_handlers(self):
        # "Персонал" and "Права" are handled by main.py, which then calls handle_role_management for "Права"

        # Handler for "Добавить пользователя" button
        @self.bot.message_handler(func=lambda message: message.text == "Добавить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin')
        def add_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Добавить пользователя'.")
            from buttons import create_cancel_markup # Local import to avoid circular if buttons imports this
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ADD_USER_DETAILS
            self.bot.send_message(chat_id, "Введите данные нового пользователя в формате: Имя Фамилия, +7XXXXXXXXXX (телефон):", reply_markup=create_cancel_markup())

        # Handler for processing the details for adding a user
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS)
        def process_add_user_data_handler(message):
            chat_id = message.chat.id
            text = message.text.strip()

            if text == "Отмена":
                self.cancel_user_management_operation(chat_id, "Добавление пользователя отменено.")
                return
            try:
                parts = text.split(",", 1) # Split only on the first comma
                if len(parts) != 2:
                    self.bot.send_message(chat_id, "Неверный формат. Используйте: Имя Фамилия, +7XXXXXXXXXX. Попробуйте снова или 'Отмена'.")
                    return # Remain in UM_STATE_AWAITING_ADD_USER_DETAILS state
                
                name_surname = parts[0].strip()
                phone = parts[1].strip()

                # Basic phone validation (e.g., starts with +7, has 11 digits after +)
                if not (phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit()):
                    self.bot.send_message(chat_id, "Неверный формат телефона. Используйте: +7XXXXXXXXXX. Попробуйте снова или 'Отмена'.")
                    return
                
                # Assume new users are 'cashier' by default, or prompt for role if needed
                response_message = add_user_to_db(phone, "cashier", name_surname)
                self.bot.send_message(chat_id, response_message)
                self.finish_user_management_operation(chat_id) # Clear state and show staff menu
            except Exception as e:
                self.logger.exception(f"Chat {chat_id}: Error adding user.")
                self.bot.send_message(chat_id, "Произошла ошибка при добавлении пользователя. Попробуйте снова.")
                self.finish_user_management_operation(chat_id)


        # Handler for "Удалить пользователя" button
        @self.bot.message_handler(func=lambda message: message.text == "Удалить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin')
        def delete_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Удалить пользователя'.")
            from buttons import create_cancel_markup
            global_main_user_state[chat_id] = UM_STATE_AWAITING_DELETE_USER_PHONE
            self.bot.send_message(chat_id, "Введите номер телефона пользователя, которого хотите деактивировать (в формате +7XXXXXXXXXX):", reply_markup=create_cancel_markup())

        # Handler for processing phone number for deleting a user
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_DELETE_USER_PHONE)
        def process_delete_user_phone_handler(message):
            chat_id = message.chat.id
            phone_to_delete = message.text.strip()

            if phone_to_delete == "Отмена":
                self.cancel_user_management_operation(chat_id, "Удаление (деактивация) пользователя отменено.")
                return
            
            # Basic phone validation
            if not (phone_to_delete.startswith("+7") and len(phone_to_delete) == 12 and phone_to_delete[1:].isdigit()):
                self.bot.send_message(chat_id, "Неверный формат телефона. Используйте: +7XXXXXXXXXX. Попробуйте снова или 'Отмена'.")
                return # Remain in UM_STATE_AWAITING_DELETE_USER_PHONE state

            try:
                response_message = deactivate_user_in_db(phone_to_delete)
                self.bot.send_message(chat_id, response_message)
                self.finish_user_management_operation(chat_id)
            except Exception as e:
                self.logger.exception(f"Chat {chat_id}: Error deactivating user {phone_to_delete}.")
                self.bot.send_message(chat_id, "Произошла ошибка при деактивации пользователя.")
                self.finish_user_management_operation(chat_id)


    def handle_role_management(self, chat_id): # This is called by main.py for "Права"
        self.logger.debug(f"Handling role management for chat_id: {chat_id}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT' # Set global state for inline keyboard interaction

        try:
            users = self.get_all_users_function()
            if not users:
                self.bot.send_message(chat_id, "Активные пользователи не найдены.")
                self.finish_user_management_operation(chat_id) # Go back to staff menu
                return

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            for user_dict in users: # Assuming get_all_users_function returns list of dicts
                button_text = f"{user_dict.get('name', 'N/A')} ({user_dict.get('role', 'N/A')}) - {user_dict.get('phone_number', 'N/A')}"
                callback_data = f"select_user_{user_dict['id']}"
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
            
            # "Назад" for inline means back to staff menu, "Отмена" also to staff menu
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_to_staff_menu"))
            self.bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)

        except Exception as e:
            self.logger.exception(f"Error in handle_role_management: {str(e)}")
            self.bot.send_message(chat_id, "Произошла ошибка при получении списка пользователей.")
            self.finish_user_management_operation(chat_id)

    def handle_select_user_callback(self, call: types.CallbackQuery): # Renamed to avoid conflict if used elsewhere
        chat_id = call.message.chat.id
        self.logger.debug(f"Chat {chat_id}: Handling user selection for role change via callback: {call.data}")

        if call.data == "um_cancel_to_staff_menu":
            self.bot.delete_message(chat_id, call.message.message_id) # Clean up inline keyboard message
            self.finish_user_management_operation(chat_id, "Изменение прав отменено.")
            return

        try:
            user_id_to_change = call.data.split("_")[-1]
            # Fetch user details to display current role if needed, or just show role options
            # current_user_for_change = next((u for u in self.get_all_users_function() if str(u['id']) == user_id_to_change), None)
            # user_name = current_user_for_change.get('name', 'Пользователь') if current_user_for_change else 'Пользователь'

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            roles = ['admin', 'cashier'] # Available roles to switch to
            for role in roles:
                keyboard.add(types.InlineKeyboardButton(
                    text=role.capitalize(),
                    callback_data=f"change_role_{user_id_to_change}_{role}"
                ))
            keyboard.add(types.InlineKeyboardButton(text="Отмена", callback_data="um_cancel_to_staff_menu")) # Back to staff menu
            
            self.bot.edit_message_text(
                # text=f"Выберите новую роль для {user_name}:",
                text="Выберите новую роль:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=keyboard
            )
        except Exception as e:
            self.logger.exception(f"Error in handle_select_user_callback: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка")
            self.finish_user_management_operation(chat_id)


    def handle_change_role_callback(self, call: types.CallbackQuery): # Renamed for clarity
        chat_id = call.message.chat.id
        self.logger.debug(f"Chat {chat_id}: Handling role change confirmation via callback: {call.data}")
        
        if call.data == "um_cancel_to_staff_menu": # Should be handled by previous if not an explicit button here
            self.bot.delete_message(chat_id, call.message.message_id)
            self.finish_user_management_operation(chat_id, "Изменение прав отменено.")
            return

        try:
            parts = call.data.split("_")
            user_id_to_change = parts[2]
            new_role = parts[3]
            
            self.change_user_role_function(user_id_to_change, new_role) # This is the DB call
            self.bot.edit_message_text(
                text=f"Роль пользователя ID {user_id_to_change} успешно изменена на {new_role}.",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=None # Clear inline keyboard
            )
            self.logger.info(f"Chat {chat_id}: Role for user ID {user_id_to_change} changed to {new_role}.")
        except Exception as e:
            self.logger.exception(f"Error in handle_change_role_callback: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка при изменении роли.")
        finally:
            # After role change or error, always go back to staff menu
            self.finish_user_management_operation(chat_id)


    def cancel_user_management_operation(self, chat_id, message_text="Операция с персоналом отменена."):
        self.logger.info(f"Chat {chat_id}: User management operation cancelled.")
        if global_main_user_state.get(chat_id, "").startswith("UM_") or global_main_user_state.get(chat_id) == 'USER_MANAGEMENT':
            global_main_user_state.pop(chat_id, None)
        
        from buttons import create_staff_management_buttons # Local import
        self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())

    def finish_user_management_operation(self, chat_id, message_text=None):
        """Clears state and returns to staff management menu."""
        self.logger.debug(f"Chat {chat_id}: Finishing user management operation.")
        if global_main_user_state.get(chat_id, "").startswith("UM_") or global_main_user_state.get(chat_id) == 'USER_MANAGEMENT':
            global_main_user_state.pop(chat_id, None)
        
        from buttons import create_staff_management_buttons # Local import
        if message_text:
            self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())
        else: # If no specific message, just show the menu (e.g. after successful op)
            self.bot.send_message(chat_id, "Выберите следующее действие:", reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self): # These are for inline buttons from "Права"
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("select_user_") or call.data == "um_cancel_to_staff_menu")
        def callback_select_user(call):
            self.handle_select_user_callback(call)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("change_role_"))
        def callback_change_role(call):
            self.handle_change_role_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers() # Registers message handlers for "Добавить", "Удалить"
    um_instance.register_callback_handlers() # Registers callback handlers for "Права" inline buttons
    return um_instance