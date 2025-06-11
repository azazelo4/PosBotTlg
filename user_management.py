from telebot import types
from database import add_user_to_db, deactivate_user_in_db, update_user_phone, get_user_by_phone # get_user_by_phone needed
import logging
from state import user_state as global_main_user_state, user_data as global_main_user_data 
from buttons import create_staff_management_buttons, create_cancel_markup

logger = logging.getLogger(__name__)

UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
# UM_STATE_AWAITING_DELETE_USER_PHONE is removed as "Удалить пользователя" button is removed
UM_STATE_AWAITING_NEW_PHONE_FOR_USER = "UM_AWAITING_NEW_PHONE_FOR_USER"
# UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION for inline deactivation confirm

class UserManagement:
    def __init__(self, bot_instance, global_user_data_ref, get_users_func, change_role_func):
        self.bot = bot_instance
        self.global_user_data = global_user_data_ref
        self.get_all_users_function = get_users_func
        self.change_user_role_function = change_role_func 
        self.logger = logging.getLogger(__name__)
        self.current_operation_data = {} 


    def configure_handlers(self):
        @self.bot.message_handler(func=lambda message: message.text == "Добавить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   self.global_user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
        def add_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Добавить пользователя'.")
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ADD_USER_DETAILS
            # Assuming admin adding user implies user is for their store_id
            admin_store_id = self.global_user_data.get(chat_id, {}).get('store_id')
            self.current_operation_data[chat_id] = {'admin_store_id': admin_store_id} # Store admin's store_id
            self.bot.send_message(chat_id, "Введите данные нового пользователя (Имя Фамилия, +7XXXXXXXXXX, Роль (admin/cashier/manager)):", reply_markup=create_cancel_markup())

        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS)
        def process_add_user_data_handler(message):
            chat_id = message.chat.id; text = message.text.strip()
            if text == "Отмена": self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление отменено."); return
            try:
                parts = text.split(",", 2) # Expect 3 parts now: Name, Phone, Role
                if len(parts) != 3: 
                    self.bot.send_message(chat_id, "Формат: Имя Фамилия, +7XXXXXXXXXX, Роль.", reply_markup=create_cancel_markup()); return
                
                name_surname, phone, role_text = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()

                if not (phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit()):
                    self.bot.send_message(chat_id, "Телефон: +7XXXXXXXXXX.", reply_markup=create_cancel_markup()); return
                if role_text not in ['admin', 'cashier', 'manager']:
                    self.bot.send_message(chat_id, "Роль: admin, cashier или manager.", reply_markup=create_cancel_markup()); return

                admin_store_id = self.current_operation_data.get(chat_id, {}).get('admin_store_id', 1) # Default to 1 if not found
                
                response_msg = add_user_to_db(phone, role_text, name_surname, admin_store_id) # Pass store_id
                self.bot.send_message(chat_id, response_msg)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e: 
                self.logger.exception(f"Chat {chat_id}: Error adding user.")
                self.bot.send_message(chat_id, f"Ошибка добавления: {e}")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
        
        # Removed "Удалить пользователя" by phone handler as button is removed.
        # Deactivation is now through "Права" -> select user -> "Деактивировать".

        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_NEW_PHONE_FOR_USER)
        def process_new_phone_input(message: types.Message):
            chat_id = message.chat.id
            new_phone_text = message.text.strip()
            operation_data = self.current_operation_data.get(chat_id, {})
            user_id_being_edited = operation_data.get('user_id_to_edit')
            original_inline_message_id = operation_data.get('inline_message_id')


            if new_phone_text == "Отмена":
                global_main_user_state.pop(chat_id, None) # Clear current state
                if user_id_being_edited and original_inline_message_id:
                    # Re-send the action menu for the user by editing the original message
                    # This requires storing and retrieving the original_inline_message_id correctly.
                    # For simplicity, we might just send a new action menu or go back to user list.
                    self.bot.delete_message(chat_id, message.message_id) # Delete "Отмена" message
                    self._send_user_action_menu_as_new(chat_id, user_id_being_edited, "Изменение телефона отменено.")
                else:
                    self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
                return

            if not (new_phone_text.startswith("+7") and len(new_phone_text) == 12 and new_phone_text[1:].isdigit()):
                self.bot.send_message(chat_id, "Неверный формат телефона (+7XXXXXXXXXX). Введите снова или 'Отмена':", reply_markup=create_cancel_markup())
                return

            if not user_id_being_edited:
                self.logger.error(f"Chat {chat_id}: State is {UM_STATE_AWAITING_NEW_PHONE_FOR_USER} but no user_id_to_edit found.")
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Произошла ошибка, операция отменена.")
                return
            
            success, db_message = update_user_phone(user_id_being_edited, new_phone_text)
            self.bot.send_message(chat_id, db_message, reply_markup=create_staff_management_buttons()) # ReplyMarkup to remove "Отмена"
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def handle_role_management(self, chat_id): 
        self.logger.debug(f"UserManagement instance handling 'Права' for chat_id: {chat_id}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_USER_LIST' # New state for user list
        self.current_operation_data.pop(chat_id, None) 

        try:
            active_users = self.get_all_users_function()
            if not active_users:
                self.bot.send_message(chat_id, "Активные пользователи не найдены.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id); return
            
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            for user_dict in active_users:
                button_text = f"{user_dict.get('name', 'N/A')} ({user_dict.get('role', 'N/A')}) - {user_dict.get('phone_number', 'N/A')}"
                callback_data = f"um_select_user_{user_dict['id']}" 
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_inline_to_staff_menu"))
            sent_message = self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
            self.current_operation_data[chat_id] = {'inline_message_id': sent_message.message_id} # Store message ID to edit later
        except Exception as e:
            self.logger.exception(f"Error in UserManagement.handle_role_management: {e}")
            self.bot.send_message(chat_id, "Ошибка получения списка пользователей.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def _send_user_action_menu_as_new(self, chat_id, user_id_to_edit, message_text_intro="Выберите действие:"):
        """Sends the user action menu as a new message."""
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ACTION_SELECT'
        # Ensure user_id_to_edit is in current_operation_data or add it
        if chat_id not in self.current_operation_data: self.current_operation_data[chat_id] = {}
        self.current_operation_data[chat_id]['user_id_to_edit'] = user_id_to_edit
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Изменить роль", callback_data=f"um_action_changerole_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Изменить телефон", callback_data=f"um_action_changephone_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Деактивировать", callback_data=f"um_action_deactivate_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к списку)", callback_data="um_action_back_to_user_list"))
        
        sent_msg = self.bot.send_message(chat_id, message_text_intro, reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id # Store new message_id


    def handle_user_selection_callback(self, call: types.CallbackQuery): 
        chat_id = call.message.chat.id
        message_id = call.message.message_id 
        self.logger.debug(f"Chat {chat_id}: User selected via callback: {call.data}")

        if call.data == "um_cancel_inline_to_staff_menu":
            self.bot.delete_message(chat_id, message_id)
            self.finish_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
            return
        
        try:
            user_id_to_edit = call.data.split("_")[-1]
            # Store user_id and original message_id for potential edits or context
            self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit, 'inline_message_id': message_id}
            
            self.bot.delete_message(chat_id, message_id) 
            self._send_user_action_menu_as_new(chat_id, user_id_to_edit, f"Управление пользователем ID {user_id_to_edit}:")
            self.bot.answer_callback_query(call.id)

        except Exception as e:
            self.logger.exception(f"Error in handle_user_selection_callback: {e}")
            self.bot.answer_callback_query(call.id, "Ошибка выбора пользователя.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def handle_user_action_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        action_data = call.data
        message_id = call.message.message_id # This is the ID of the action menu message
        self.logger.debug(f"Chat {chat_id}: User action callback: {action_data}")

        user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
             try: user_id_to_edit = action_data.split("_")[-1] # Attempt to re-parse if context lost
             except IndexError: pass # Keep it None if parsing fails
        
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
            self.logger.error(f"Chat {chat_id}: Could not determine user_id for action '{action_data}'. current_op_data: {self.current_operation_data.get(chat_id)}")
            self.bot.edit_message_text("Ошибка: пользователь не определен. Операция прервана.", chat_id, message_id, reply_markup=None)
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
            return
        
        self.bot.delete_message(chat_id, message_id) # Delete the action menu message

        if action_data.startswith("um_action_changerole_"):
            self._prompt_for_new_role(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_changephone_"):
            self._prompt_for_new_phone(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_deactivate_"):
            self._prompt_for_deactivation_confirm(chat_id, user_id_to_edit)
        elif action_data == "um_action_back_to_user_list":
            self.handle_role_management(chat_id) # Re-display user list (sends a new message)
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
        self.bot.answer_callback_query(call.id)


    def _prompt_for_new_role(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ROLE_SELECT' 
        self.current_operation_data[chat_id].update({'action': 'change_role', 'user_id_to_edit': user_id_to_edit})

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        roles = ['admin', 'cashier', 'manager'] # Added manager
        for role in roles:
            keyboard.add(types.InlineKeyboardButton(
                text=role.capitalize(),
                callback_data=f"um_setrole_{user_id_to_edit}_{role}" 
            ))
        # Back button should go to the action menu for the *same user*
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}"))
        sent_msg = self.bot.send_message(chat_id, f"Выберите новую роль для пользователя ID {user_id_to_edit}:", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_set_new_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        message_id = call.message.message_id # message with role options
        data_parts = call.data.split("_") 
        user_id_to_change = data_parts[2]
        new_role = data_parts[3]

        self.logger.info(f"Chat {chat_id}: Attempting to set role for user {user_id_to_change} to {new_role}")
        success, db_message = self.change_user_role_function(user_id_to_change, new_role)
        
        self.bot.edit_message_text(text=db_message, chat_id=chat_id, message_id=message_id, reply_markup=None)
        self.bot.answer_callback_query(call.id, text=db_message if success else "Ошибка")
        self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def _prompt_for_new_phone(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = UM_STATE_AWAITING_NEW_PHONE_FOR_USER
        self.current_operation_data[chat_id].update({'action': 'change_phone', 'user_id_to_edit': user_id_to_edit})
        self.bot.send_message(chat_id, f"Введите новый номер телефона для пользователя ID {user_id_to_edit} (формат: +7XXXXXXXXXX) или 'Отмена':", reply_markup=create_cancel_markup())

    def _prompt_for_deactivation_confirm(self, chat_id, user_id_to_deactivate):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_DEACTIVATE_CONFIRM' # Specific state
        self.current_operation_data[chat_id].update({'action': 'deactivate', 'user_id_to_deactivate': user_id_to_deactivate})

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Да, деактивировать", callback_data=f"um_confirm_deactivate_{user_id_to_deactivate}"))
        # Back button should go to the action menu for the *same user*
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_deactivate}"))
        sent_msg = self.bot.send_message(chat_id, f"Вы уверены, что хотите деактивировать пользователя ID {user_id_to_deactivate}?", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_deactivation_confirmation_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        message_id = call.message.message_id # message with confirm/cancel buttons
        user_id_to_deactivate = call.data.split("_")[-1]

        self.logger.info(f"Chat {chat_id}: Deactivating user ID {user_id_to_deactivate}")
        response_message = deactivate_user_in_db(user_id_to_deactivate)
        
        self.bot.edit_message_text(text=response_message, chat_id=chat_id, message_id=message_id, reply_markup=None)
        self.bot.answer_callback_query(call.id, text=response_message)
        self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def cancel_um_operation_and_return_to_staff_menu(self, chat_id, message_text="Операция с персоналом отменена."):
        # ... (remains largely the same) ...
        self.logger.info(f"Chat {chat_id}: User management operation cancelled. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state.startswith("USER_MANAGEMENT"):
            global_main_user_state.pop(chat_id, None)
        self.current_operation_data.pop(chat_id, None) 
        self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())


    def finish_um_operation_and_return_to_staff_menu(self, chat_id, message_text=None):
        # ... (remains largely the same) ...
        self.logger.debug(f"Chat {chat_id}: Finishing UM operation. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state.startswith("USER_MANAGEMENT"):
            global_main_user_state.pop(chat_id, None)
        self.current_operation_data.pop(chat_id, None)
        
        reply_message = message_text if message_text else "Выберите следующее действие в меню персонала:"
        self.bot.send_message(chat_id, reply_message, reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self):
        # um_select_user_ID (selects a user from list, shows action menu)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_select_user_") or \
                                                           call.data == "um_cancel_inline_to_staff_menu")
        def route_user_selection_callback(call):
            if call.data == "um_cancel_inline_to_staff_menu":
                 self.bot.delete_message(call.message.chat.id, call.message.message_id)
                 self.finish_um_operation_and_return_to_staff_menu(call.message.chat.id, "Операция отменена.")
            else:
                self.handle_user_selection_callback(call)

        # um_action_ACTION_ID (selects an action for the user)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_action_"))
        def route_user_action_callback(call):
            self.handle_user_action_callback(call)

        # um_setrole_ID_ROLE (confirms the new role)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_setrole_"))
        def route_set_new_role_callback(call):
            self.handle_set_new_role_callback(call)
        
        # um_confirm_deactivate_ID (confirms deactivation)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_confirm_deactivate_"))
        def route_deactivation_confirm_callback(call):
            self.handle_deactivation_confirmation_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers()
    um_instance.register_callback_handlers()
    return um_instance