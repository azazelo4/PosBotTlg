from telebot import types
from database import add_user_to_db, deactivate_user_in_db 
import logging
from state import user_state as global_main_user_state # Use the main user_state for FSM
from buttons import create_staff_management_buttons, create_cancel_markup # Import necessary buttons

logger = logging.getLogger(__name__)

UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
UM_STATE_AWAITING_DELETE_USER_PHONE = "UM_AWAITING_DELETE_USER_PHONE"
# 'USER_MANAGEMENT' state is set by handle_role_management for inline keyboard interaction

class UserManagement:
    def __init__(self, bot_instance, global_user_data_ref, get_users_func, change_role_func):
        self.bot = bot_instance
        self.global_user_data = global_user_data_ref
        self.get_all_users_function = get_users_func
        self.change_user_role_function = change_role_func
        self.logger = logging.getLogger(__name__)
        # No self.user_state anymore, using global_main_user_state from state.py

    def configure_handlers(self):
        # Handler for "Добавить пользователя" button
        @self.bot.message_handler(func=lambda message: message.text == "Добавить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   self.global_user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
        def add_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Добавить пользователя'.")
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ADD_USER_DETAILS
            self.bot.send_message(chat_id, "Введите данные нового пользователя в формате: Имя Фамилия, +7XXXXXXXXXX (телефон):", reply_markup=create_cancel_markup())

        # Handler for processing the details for adding a user
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS)
        def process_add_user_data_handler(message):
            chat_id = message.chat.id
            text = message.text.strip()

            if text == "Отмена": # Direct Отмена handling
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление пользователя отменено.")
                return
            try:
                parts = text.split(",", 1)
                if len(parts) != 2:
                    self.bot.send_message(chat_id, "Неверный формат. Используйте: Имя Фамилия, +7XXXXXXXXXX. Попробуйте снова или 'Отмена'.", reply_markup=create_cancel_markup())
                    return 
                
                name_surname = parts[0].strip()
                phone = parts[1].strip()

                if not (phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit()):
                    self.bot.send_message(chat_id, "Неверный формат телефона. Используйте: +7XXXXXXXXXX. Попробуйте снова или 'Отмена'.", reply_markup=create_cancel_markup())
                    return
                
                response_message = add_user_to_db(phone, "cashier", name_surname) # Default role 'cashier'
                self.bot.send_message(chat_id, response_message)
                self.finish_um_operation_and_return_to_staff_menu(chat_id) 
            except Exception as e:
                self.logger.exception(f"Chat {chat_id}: Error adding user.")
                self.bot.send_message(chat_id, "Произошла ошибка при добавлении пользователя.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)


        # Handler for "Удалить пользователя" button
        @self.bot.message_handler(func=lambda message: message.text == "Удалить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   self.global_user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
        def delete_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Удалить пользователя'.")
            global_main_user_state[chat_id] = UM_STATE_AWAITING_DELETE_USER_PHONE
            self.bot.send_message(chat_id, "Введите номер телефона пользователя для деактивации (+7XXXXXXXXXX):", reply_markup=create_cancel_markup())

        # Handler for processing phone number for deleting a user
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_DELETE_USER_PHONE)
        def process_delete_user_phone_handler(message):
            chat_id = message.chat.id
            phone_to_delete = message.text.strip()

            if phone_to_delete == "Отмена": # Direct Отмена handling
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Деактивация пользователя отменена.")
                return
            
            if not (phone_to_delete.startswith("+7") and len(phone_to_delete) == 12 and phone_to_delete[1:].isdigit()):
                self.bot.send_message(chat_id, "Неверный формат телефона (+7XXXXXXXXXX). Попробуйте снова или 'Отмена'.", reply_markup=create_cancel_markup())
                return 

            try:
                response_message = deactivate_user_in_db(phone_to_delete)
                self.bot.send_message(chat_id, response_message)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e:
                self.logger.exception(f"Chat {chat_id}: Error deactivating user {phone_to_delete}.")
                self.bot.send_message(chat_id, "Произошла ошибка при деактивации пользователя.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)

    # This method is called by main.py when "Права" button is pressed
    def handle_role_management(self, chat_id): 
        self.logger.debug(f"UserManagement instance handling 'Права' for chat_id: {chat_id}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT'

        try:
            active_users = self.get_all_users_function() # Fetches only active users
            if not active_users:
                self.bot.send_message(chat_id, "Активные пользователи не найдены.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
                return

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            for user_dict in active_users:
                button_text = f"{user_dict.get('name', 'N/A')} ({user_dict.get('role', 'N/A')}) - {user_dict.get('phone_number', 'N/A')}"
                callback_data = f"select_user_{user_dict['id']}" # Ensure 'id' is the DB user ID
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
            
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_inline_to_staff_menu"))
            self.bot.send_message(chat_id, "Выберите пользователя для изменения роли:", reply_markup=keyboard)

        except Exception as e:
            self.logger.exception(f"Error in UserManagement.handle_role_management: {str(e)}")
            self.bot.send_message(chat_id, "Произошла ошибка при получении списка пользователей.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def handle_select_user_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        self.logger.debug(f"Chat {chat_id}: Handling user selection for role change via callback: {call.data}")

        if call.data == "um_cancel_inline_to_staff_menu":
            self.bot.delete_message(chat_id, call.message.message_id) 
            self.finish_um_operation_and_return_to_staff_menu(chat_id, "Изменение прав отменено.")
            return

        try:
            user_id_to_change = call.data.split("_")[-1]
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            roles = ['admin', 'cashier'] 
            for role in roles:
                keyboard.add(types.InlineKeyboardButton(
                    text=role.capitalize(),
                    callback_data=f"change_role_{user_id_to_change}_{role}"
                ))
            keyboard.add(types.InlineKeyboardButton(text="Отмена", callback_data="um_cancel_inline_to_staff_menu"))
            
            self.bot.edit_message_text(
                text="Выберите новую роль:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=keyboard
            )
        except Exception as e:
            self.logger.exception(f"Error in handle_select_user_callback: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def handle_change_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        self.logger.debug(f"Chat {chat_id}: Handling role change confirmation via callback: {call.data}")
        
        if call.data == "um_cancel_inline_to_staff_menu": 
            self.bot.delete_message(chat_id, call.message.message_id)
            self.finish_um_operation_and_return_to_staff_menu(chat_id, "Изменение прав отменено.")
            return

        try:
            parts = call.data.split("_")
            user_id_to_change = parts[2]
            new_role = parts[3]
            
            self.change_user_role_function(user_id_to_change, new_role) 
            self.bot.edit_message_text(
                text=f"Роль пользователя ID {user_id_to_change} успешно изменена на {new_role}.",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=None 
            )
            self.logger.info(f"Chat {chat_id}: Role for user ID {user_id_to_change} changed to {new_role}.")
        except Exception as e:
            self.logger.exception(f"Error in handle_change_role_callback: {str(e)}")
            self.bot.answer_callback_query(call.id, "Произошла ошибка при изменении роли.")
        finally:
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def cancel_um_operation_and_return_to_staff_menu(self, chat_id, message_text="Операция с персоналом отменена."):
        self.logger.info(f"Chat {chat_id}: User management operation cancelled. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state == 'USER_MANAGEMENT':
            global_main_user_state.pop(chat_id, None)
        self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())

    def finish_um_operation_and_return_to_staff_menu(self, chat_id, message_text=None):
        self.logger.debug(f"Chat {chat_id}: Finishing UM operation. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state == 'USER_MANAGEMENT':
            global_main_user_state.pop(chat_id, None)
        
        reply_message = message_text if message_text else "Выберите следующее действие в меню персонала:"
        self.bot.send_message(chat_id, reply_message, reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self): 
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("select_user_") or call.data == "um_cancel_inline_to_staff_menu")
        def route_select_user_callback(call): # Renamed to avoid direct recursion if not careful
            self.handle_select_user_callback(call)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("change_role_"))
        def route_change_role_callback(call): # Renamed
            self.handle_change_role_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers() 
    um_instance.register_callback_handlers() 
    return um_instance