from telebot import types
from database import add_user_to_db, deactivate_user_in_db, update_user_phone # Added update_user_phone
import logging
from state import user_state as global_main_user_state, user_data as global_main_user_data # Use the main user_state for FSM
from buttons import create_staff_management_buttons, create_cancel_markup

logger = logging.getLogger(__name__)

UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
UM_STATE_AWAITING_DELETE_USER_PHONE = "UM_AWAITING_DELETE_USER_PHONE" # Not used if deactivating by ID from inline menu
UM_STATE_AWAITING_NEW_PHONE_FOR_USER = "UM_AWAITING_NEW_PHONE_FOR_USER"
UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION = "UM_AWAITING_DEACTIVATE_CONFIRMATION"

# USER_MANAGEMENT state (for inline keyboard) is set by handle_role_management
# UserManagement class now handles the states after a user is selected from "Права"

class UserManagement:
    def __init__(self, bot_instance, global_user_data_ref, get_users_func, change_role_func):
        self.bot = bot_instance
        self.global_user_data = global_user_data_ref
        self.get_all_users_function = get_users_func
        self.change_user_role_function = change_role_func # This is change_user_role_function from database.py
        self.logger = logging.getLogger(__name__)
        # Temporary storage for operations within this class instance, if needed (e.g., user_id being edited)
        self.current_operation_data = {} # chat_id -> {'user_id_to_edit': id, 'action': 'change_role'/'change_phone' etc.}


    def configure_handlers(self):
        # --- Message Handlers for "Добавить пользователя" and "Удалить пользователя" (by phone) ---
        @self.bot.message_handler(func=lambda message: message.text == "Добавить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   self.global_user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
        def add_user_prompt_handler(message):
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Добавить пользователя'.")
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ADD_USER_DETAILS
            self.bot.send_message(chat_id, "Введите данные нового пользователя (Имя Фамилия, +7XXXXXXXXXX):", reply_markup=create_cancel_markup())

        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS)
        def process_add_user_data_handler(message):
            chat_id = message.chat.id; text = message.text.strip()
            if text == "Отмена": self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление отменено."); return
            try:
                parts = text.split(",", 1);
                if len(parts) != 2: self.bot.send_message(chat_id, "Формат: Имя Фамилия, +7XXXXXXXXXX.", reply_markup=create_cancel_markup()); return
                name_surname, phone = parts[0].strip(), parts[1].strip()
                if not (phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit()):
                    self.bot.send_message(chat_id, "Телефон: +7XXXXXXXXXX.", reply_markup=create_cancel_markup()); return
                response_msg = add_user_to_db(phone, "cashier", name_surname)
                self.bot.send_message(chat_id, response_msg)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e: self.logger.exception(f"Chat {chat_id}: Error adding user."); self.bot.send_message(chat_id, "Ошибка добавления."); self.finish_um_operation_and_return_to_staff_menu(chat_id)

        @self.bot.message_handler(func=lambda message: message.text == "Удалить пользователя" and \
                                   self.global_user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   self.global_user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
        def delete_user_by_phone_prompt(message): # This is the old "Удалить пользователя" by phone
            chat_id = message.chat.id
            self.logger.info(f"Chat {chat_id}: Admin initiated 'Удалить пользователя' (by phone).")
            global_main_user_state[chat_id] = UM_STATE_AWAITING_DELETE_USER_PHONE # Kept for this flow
            self.bot.send_message(chat_id, "Введите номер телефона для деактивации (+7XXXXXXXXXX):", reply_markup=create_cancel_markup())

        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_DELETE_USER_PHONE)
        def process_delete_user_by_phone(message):
            chat_id = message.chat.id; phone_to_delete = message.text.strip()
            if phone_to_delete == "Отмена": self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Деактивация отменена."); return
            if not (phone_to_delete.startswith("+7") and len(phone_to_delete) == 12 and phone_to_delete[1:].isdigit()):
                self.bot.send_message(chat_id, "Телефон: +7XXXXXXXXXX.", reply_markup=create_cancel_markup()); return
            try:
                # Note: deactivate_user_in_db was changed to accept user_id.
                # For this flow, we need to get user_id from phone first if we want to use that specific function.
                # Or, keep a version of deactivate_user_in_db that takes a phone number.
                # For now, assuming direct deactivation by phone if that's still desired for this button.
                # If you want to unify, this button should also list users.
                # Let's assume for now this is a separate "deactivate by phone" action.
                # To use the ID-based one:
                user_to_deactivate = get_user_by_phone(phone_to_delete) # from database.py
                if user_to_deactivate and user_to_deactivate.get('id'):
                    response_msg = deactivate_user_in_db(user_to_deactivate['id'])
                else:
                    response_msg = f"Пользователь с номером {phone_to_delete} не найден."
                self.bot.send_message(chat_id, response_msg)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e: self.logger.exception(f"Chat {chat_id}: Error deactivating user by phone {phone_to_delete}."); self.bot.send_message(chat_id, "Ошибка деактивации."); self.finish_um_operation_and_return_to_staff_menu(chat_id)

        # --- Message Handler for new phone number input ---
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_NEW_PHONE_FOR_USER)
        def process_new_phone_input(message: types.Message):
            chat_id = message.chat.id
            new_phone_text = message.text.strip()

            if new_phone_text == "Отмена":
                # Go back to user action selection menu, not staff menu
                user_id_being_edited = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
                if user_id_being_edited:
                    self.bot.delete_message(chat_id, message.message_id) # Clean up "Отмена" reply keyboard
                    self._send_user_action_menu(chat_id, user_id_being_edited, "Изменение телефона отменено.")
                else: # Should not happen if state is correct
                    self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
                return

            if not (new_phone_text.startswith("+7") and len(new_phone_text) == 12 and new_phone_text[1:].isdigit()):
                self.bot.send_message(chat_id, "Неверный формат телефона (+7XXXXXXXXXX). Введите снова или 'Отмена':", reply_markup=create_cancel_markup())
                return

            user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
            if not user_id_to_edit:
                self.logger.error(f"Chat {chat_id}: State is {UM_STATE_AWAITING_NEW_PHONE_FOR_USER} but no user_id_to_edit found.")
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Произошла ошибка, операция отменена.")
                return
            
            success, db_message = update_user_phone(user_id_to_edit, new_phone_text)
            self.bot.send_message(chat_id, db_message)
            # After updating, go back to the staff menu or user action menu? For now, staff menu.
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    # --- Methods for "Права" flow (mostly inline keyboards) ---
    def handle_role_management(self, chat_id): # Called by main.py for "Права" button
        self.logger.debug(f"UserManagement instance handling 'Права' for chat_id: {chat_id}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT' # Indicates "Права" flow is active
        self.current_operation_data.pop(chat_id, None) # Clear any previous op data for this chat

        try:
            active_users = self.get_all_users_function()
            if not active_users:
                self.bot.send_message(chat_id, "Активные пользователи не найдены.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id); return
            
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            for user_dict in active_users:
                button_text = f"{user_dict.get('name', 'N/A')} ({user_dict.get('role', 'N/A')}) - {user_dict.get('phone_number', 'N/A')}"
                callback_data = f"um_select_user_{user_dict['id']}" # Prefixed for clarity
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_inline_to_staff_menu"))
            self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
        except Exception as e:
            self.logger.exception(f"Error in UserManagement.handle_role_management: {e}")
            self.bot.send_message(chat_id, "Ошибка получения списка пользователей.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def _send_user_action_menu(self, chat_id, user_id_to_edit, previous_message_text=None):
        """Sends the menu of actions for a selected user."""
        self.logger.debug(f"Chat {chat_id}: Sending action menu for user ID {user_id_to_edit}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ACTION_SELECT' # New state
        self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit}

        # You might want to fetch user's current name/phone to display in the message
        # For now, a generic message
        message_text = previous_message_text or f"Выберите действие для пользователя ID {user_id_to_edit}:"
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Изменить роль", callback_data=f"um_action_changerole_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Изменить телефон", callback_data=f"um_action_changephone_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Деактивировать", callback_data=f"um_action_deactivate_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к списку)", callback_data="um_action_back_to_user_list"))
        
        # Check if the message that triggered this was an inline keyboard button press
        # If so, edit that message. Otherwise, send a new one.
        # This requires storing the message_id of the user list if we want to edit it.
        # For simplicity now, we'll assume we always send a new message or edit if possible.
        # If previous_message_text is provided, it implies we are editing a previous message from a cancel.
        # This part needs careful handling of message_id to correctly edit.
        # A common pattern is to store message_id in self.current_operation_data[chat_id]['last_message_id']

        # For now, let's assume we always send a new message when this menu is first shown after user selection
        # And edit if coming back from a sub-action's cancellation
        
        # A simple way: if the global state was previously something that implies an inline message was there
        # (like 'USER_MANAGEMENT_ROLE_SELECT'), then try to edit. This is fragile.
        # Better: pass the original call.message.message_id to this function if it's an edit.

        # Let's assume this is called after user selection, so we edit the "Выберите пользователя" message
        # This requires the `call` object from the user selection callback.
        # This function will be called from `handle_user_selection_callback`.
        
        # If `previous_message_text` is not None, it implies an edit operation due to cancellation
        # This part is tricky without the original `call` object.
        # A robust solution might involve passing message_id around or storing it.

        # For now, if called directly without a `call` object to edit, it sends a new message.
        # If called from a callback that *can* edit, that callback should do the edit.
        self.bot.send_message(chat_id, message_text, reply_markup=keyboard)


    def handle_user_selection_callback(self, call: types.CallbackQuery): # Renamed and modified
        chat_id = call.message.chat.id
        message_id = call.message.message_id # Message to potentially edit
        self.logger.debug(f"Chat {chat_id}: User selected via callback: {call.data}")

        if call.data == "um_cancel_inline_to_staff_menu":
            self.bot.delete_message(chat_id, message_id)
            self.finish_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
            return
        
        try:
            user_id_to_edit = call.data.split("_")[-1]
            self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit, 'message_id_for_edit': message_id}
            
            # Instead of going directly to role change, show action menu
            # We need to edit the current message (user list) to show the action menu
            self.bot.delete_message(chat_id, message_id) # Delete the user list message
            self._send_user_action_menu(chat_id, user_id_to_edit, f"Управление пользователем ID {user_id_to_edit}:")


        except Exception as e:
            self.logger.exception(f"Error in handle_user_selection_callback: {e}")
            self.bot.answer_callback_query(call.id, "Ошибка выбора пользователя.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id) # Back to staff menu

    def handle_user_action_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        action_data = call.data
        self.logger.debug(f"Chat {chat_id}: User action callback: {action_data}")

        # Extract user_id from self.current_operation_data or from callback
        user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
        original_message_id = self.current_operation_data.get(chat_id, {}).get('message_id_for_edit', call.message.message_id)


        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"): # if not user_id and not back action
            # Attempt to parse from callback if missing in current_op_data (e.g. after bot restart)
            try:
                parts = action_data.split("_")
                if len(parts) >= 3 : user_id_to_edit = parts[-1] # e.g. um_action_changerole_ID
                self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit, 'message_id_for_edit': original_message_id}
            except:
                self.logger.error(f"Chat {chat_id}: Could not determine user_id for action '{action_data}'.")
                self.bot.answer_callback_query(call.id, "Ошибка: пользователь не определен.")
                self.finish_um_operation_and_return_to_staff_menu(chat_id, "Операция прервана из-за ошибки.")
                return
        
        self.bot.delete_message(chat_id, original_message_id) # Delete the action menu

        if action_data.startswith("um_action_changerole_"):
            self._prompt_for_new_role(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_changephone_"):
            self._prompt_for_new_phone(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_deactivate_"):
            self._prompt_for_deactivation_confirm(chat_id, user_id_to_edit)
        elif action_data == "um_action_back_to_user_list":
            self.handle_role_management(chat_id) # Re-display user list
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def _prompt_for_new_role(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ROLE_SELECT' # State for callback
        self.current_operation_data[chat_id]['action'] = 'change_role' # Store current action

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        roles = ['admin', 'cashier']
        for role in roles:
            keyboard.add(types.InlineKeyboardButton(
                text=role.capitalize(),
                callback_data=f"um_setrole_{user_id_to_edit}_{role}" # Different prefix for this action
            ))
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}")) # Go back to action menu for this user
        self.bot.send_message(chat_id, f"Выберите новую роль для пользователя ID {user_id_to_edit}:", reply_markup=keyboard)

    def handle_set_new_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        data_parts = call.data.split("_") # um_setrole_USERID_NEWROLE
        user_id_to_change = data_parts[2]
        new_role = data_parts[3]
        original_message_id = call.message.message_id # Message to edit (role selection list)

        self.logger.info(f"Chat {chat_id}: Attempting to set role for user {user_id_to_change} to {new_role}")
        success, db_message = self.change_user_role_function(user_id_to_change, new_role)
        
        self.bot.edit_message_text(text=db_message, chat_id=chat_id, message_id=original_message_id, reply_markup=None)
        
        # After role change, return to staff menu
        self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def _prompt_for_new_phone(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = UM_STATE_AWAITING_NEW_PHONE_FOR_USER
        self.current_operation_data[chat_id]['action'] = 'change_phone'
        self.current_operation_data[chat_id]['user_id_to_edit'] = user_id_to_edit # Ensure it's set here too
        self.bot.send_message(chat_id, f"Введите новый номер телефона для пользователя ID {user_id_to_edit} (формат: +7XXXXXXXXXX) или 'Отмена':", reply_markup=create_cancel_markup())

    def _prompt_for_deactivation_confirm(self, chat_id, user_id_to_deactivate):
        global_main_user_state[chat_id] = UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION
        self.current_operation_data[chat_id]['action'] = 'deactivate'
        self.current_operation_data[chat_id]['user_id_to_deactivate'] = user_id_to_deactivate

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Да, деактивировать", callback_data=f"um_confirm_deactivate_{user_id_to_deactivate}"))
        keyboard.add(types.InlineKeyboardButton(text="Нет, отмена", callback_data=f"um_select_user_{user_id_to_deactivate}")) # Back to action menu for this user
        self.bot.send_message(chat_id, f"Вы уверены, что хотите деактивировать пользователя ID {user_id_to_deactivate}?", reply_markup=keyboard)

    def handle_deactivation_confirmation_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        user_id_to_deactivate = call.data.split("_")[-1]
        original_message_id = call.message.message_id

        self.logger.info(f"Chat {chat_id}: Deactivating user ID {user_id_to_deactivate}")
        response_message = deactivate_user_in_db(user_id_to_deactivate) # Accepts user_id now
        
        self.bot.edit_message_text(text=response_message, chat_id=chat_id, message_id=original_message_id, reply_markup=None)
        self.finish_um_operation_and_return_to_staff_menu(chat_id)


    def cancel_um_operation_and_return_to_staff_menu(self, chat_id, message_text="Операция с персоналом отменена."):
        self.logger.info(f"Chat {chat_id}: User management operation cancelled. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state.startswith("USER_MANAGEMENT"): # More general check
            global_main_user_state.pop(chat_id, None)
        self.current_operation_data.pop(chat_id, None) # Clear specific operation data
        self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())

    def finish_um_operation_and_return_to_staff_menu(self, chat_id, message_text=None):
        self.logger.debug(f"Chat {chat_id}: Finishing UM operation. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state.startswith("USER_MANAGEMENT"):
            global_main_user_state.pop(chat_id, None)
        self.current_operation_data.pop(chat_id, None)
        
        reply_message = message_text if message_text else "Выберите следующее действие в меню персонала:"
        self.bot.send_message(chat_id, reply_message, reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self):
        # Handles selection of a user from the list (sent by handle_role_management)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_select_user_") or \
                                                           call.data == "um_cancel_inline_to_staff_menu")
        def route_user_selection_callback(call):
            self.handle_user_selection_callback(call)

        # Handles selection of an action for the chosen user (Изменить роль, Изменить телефон, etc.)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_action_"))
        def route_user_action_callback(call):
            self.handle_user_action_callback(call)

        # Handles setting the new role after role is selected from inline keyboard
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_setrole_"))
        def route_set_new_role_callback(call):
            self.handle_set_new_role_callback(call)
        
        # Handles confirmation of deactivation
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_confirm_deactivate_"))
        def route_deactivation_confirm_callback(call):
            self.handle_deactivation_confirmation_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers()
    um_instance.register_callback_handlers()
    return um_instance