# user_management.py

from telebot import types
from database import add_user_to_db, deactivate_user_in_db, update_user_phone, get_user_by_phone # get_user_by_phone needed
import logging
from state import user_state as global_main_user_state, user_data as global_main_user_data 
from buttons import create_staff_management_buttons, create_cancel_markup

logger = logging.getLogger(__name__)

UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
# Новое состояние для ожидания роли после отправки контакта
UM_STATE_AWAITING_ROLE_FOR_CONTACT = "UM_AWAITING_ROLE_FOR_CONTACT"
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
            
            # Обновляем текст-подсказку
            self.bot.send_message(
                chat_id, 
                "Введите данные нового пользователя в формате: Имя Фамилия, +7XXXXXXXXXX, Роль (admin/cashier/manager).\n\n"
                "Или просто отправьте контакт пользователя, и я запрошу недостающие данные.", 
                reply_markup=create_cancel_markup()
            )

        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS, content_types=['text'])
        def process_add_user_data_handler(message):
            chat_id = message.chat.id; text = message.text.strip()
            if text == "Отмена": self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление отменено."); return
            try:
                parts = text.split(",", 2) # Expect 3 parts now: Name, Phone, Role
                if len(parts) != 3: 
                    self.bot.send_message(chat_id, "Неверный формат. Ожидается: Имя Фамилия, +7XXXXXXXXXX, Роль.", reply_markup=create_cancel_markup()); return
                
                name_surname, phone, role_text = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()

                if not (phone.startswith("+7") and len(phone) == 12 and phone[1:].isdigit()):
                    self.bot.send_message(chat_id, "Неверный формат телефона: +7XXXXXXXXXX.", reply_markup=create_cancel_markup()); return
                if role_text not in ['admin', 'cashier', 'manager']:
                    self.bot.send_message(chat_id, "Неверная роль. Доступные роли: admin, cashier, manager.", reply_markup=create_cancel_markup()); return

                admin_store_id = self.current_operation_data.get(chat_id, {}).get('admin_store_id', 1) # Default to 1 if not found
                
                response_msg = add_user_to_db(phone, role_text, name_surname, admin_store_id) # Pass store_id
                self.bot.send_message(chat_id, response_msg)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e: 
                self.logger.exception(f"Chat {chat_id}: Error adding user from text.")
                self.bot.send_message(chat_id, f"Ошибка добавления: {e}")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
        
        # --- НОВЫЙ ОБРАБОТЧИК ДЛЯ КОНТАКТОВ ---
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS, content_types=['contact'])
        def process_add_user_contact_handler(message: types.Message):
            chat_id = message.chat.id
            contact = message.contact
            self.logger.info(f"Chat {chat_id}: Received contact to add user: {contact.phone_number}")

            # Нормализация номера телефона
            phone_number = contact.phone_number
            if not phone_number.startswith('+'):
                phone_number = '+' + phone_number

            # Формирование имени
            full_name = contact.first_name
            if contact.last_name:
                full_name += f" {contact.last_name}"

            # Сохраняем полученные данные во временное хранилище
            op_data = self.current_operation_data.get(chat_id, {})
            op_data.update({
                'new_user_phone': phone_number,
                'new_user_name': full_name
            })
            self.current_operation_data[chat_id] = op_data

            # Меняем состояние и запрашиваем роль
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ROLE_FOR_CONTACT

            keyboard = types.InlineKeyboardMarkup()
            roles = ['admin', 'cashier', 'manager']
            for role in roles:
                keyboard.add(types.InlineKeyboardButton(text=role.capitalize(), callback_data=f"um_setrole_contact_{role}"))
            keyboard.add(types.InlineKeyboardButton(text="Отмена", callback_data="um_cancel_contact_add"))

            self.bot.send_message(chat_id, f"Контакт получен: {full_name}, {phone_number}.\n\nТеперь выберите роль для нового пользователя:", reply_markup=keyboard)
            self.bot.send_message(chat_id, "Чтобы отменить операцию, нажмите 'Отмена' на клавиатуре.", reply_markup=create_cancel_markup()) # На случай если inline-кнопки не сработают


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
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_USER_LIST'
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
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_inline_to_staff_menu")) # Corrected "назад" to "Отмена"
            sent_message = self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
            self.current_operation_data[chat_id] = {'inline_message_id': sent_message.message_id} # Store message ID to edit later
        except Exception as e:
            self.logger.exception(f"Error in UserManagement.handle_role_management: {e}")
            self.bot.send_message(chat_id, "Ошибка получения списка пользователей.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def _send_user_action_menu_as_new(self, chat_id, user_id_to_edit, message_text_intro="Выберите действие:"):
        """Sends the user action menu as a new message."""
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ACTION_SELECT'
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
        message_id = call.message.message_id 
        self.logger.debug(f"Chat {chat_id}: User action callback: {action_data}")

        user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
             try: user_id_to_edit = action_data.split("_")[-1] 
             except IndexError: pass
        
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
            self.logger.error(f"Chat {chat_id}: Could not determine user_id for action '{action_data}'. current_op_data: {self.current_operation_data.get(chat_id)}")
            self.bot.edit_message_text("Ошибка: пользователь не определен. Операция прервана.", chat_id, message_id, reply_markup=None)
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
            return
        
        self.bot.delete_message(chat_id, message_id) 

        if action_data.startswith("um_action_changerole_"):
            self._prompt_for_new_role(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_changephone_"):
            self._prompt_for_new_phone(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_deactivate_"):
            self._prompt_for_deactivation_confirm(chat_id, user_id_to_edit)
        elif action_data == "um_action_back_to_user_list":
            self.handle_role_management(chat_id)
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
        self.bot.answer_callback_query(call.id)


    def _prompt_for_new_role(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ROLE_SELECT' 
        self.current_operation_data[chat_id].update({'action': 'change_role', 'user_id_to_edit': user_id_to_edit})

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        roles = ['admin', 'cashier', 'manager']
        for role in roles:
            keyboard.add(types.InlineKeyboardButton(
                text=role.capitalize(),
                callback_data=f"um_setrole_{user_id_to_edit}_{role}" 
            ))
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}"))
        sent_msg = self.bot.send_message(chat_id, f"Выберите новую роль для пользователя ID {user_id_to_edit}:", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_set_new_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        message_id = call.message.message_id 
        data_parts = call.data.split("_") 
        user_id_to_change = data_parts[2]
        new_role = data_parts[3]

        self.logger.info(f"Chat {chat_id}: Attempting to set role for user {user_id_to_change} to {new_role}")
        success, db_message = self.change_user_role_function(user_id_to_change, new_role)
        
        self.bot.edit_message_text(text=db_message, chat_id=chat_id, message_id=message_id, reply_markup=None)
        self.bot.answer_callback_query(call.id, text=db_message if success else "Ошибка")
        self.finish_um_operation_and_return_to_staff_menu(chat_id)

    # --- НОВЫЙ ОБРАБОТЧИК ДЛЯ ВЫБОРА РОЛИ ПОСЛЕ ОТПРАВКИ КОНТАКТА ---
    def handle_set_role_for_contact_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        
        if call.data == "um_cancel_contact_add":
            self.bot.delete_message(chat_id, message_id)
            self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление пользователя отменено.")
            self.bot.answer_callback_query(call.id)
            return

        try:
            new_role = call.data.split("_")[-1]
            op_data = self.current_operation_data.get(chat_id, {})
            phone = op_data.get('new_user_phone')
            name = op_data.get('new_user_name')
            admin_store_id = op_data.get('admin_store_id', 1)

            if not phone or not name:
                self.logger.error(f"Chat {chat_id}: Missing phone or name in op_data for adding user via contact. Data: {op_data}")
                self.bot.edit_message_text("Произошла ошибка: данные контакта были утеряны. Операция отменена.", chat_id, message_id, reply_markup=None)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
                self.bot.answer_callback_query(call.id, "Ошибка")
                return

            self.logger.info(f"Chat {chat_id}: Adding user from contact. Phone: {phone}, Name: {name}, Role: {new_role}")
            response_msg = add_user_to_db(phone, new_role, name, admin_store_id)

            self.bot.edit_message_text(response_msg, chat_id, message_id, reply_markup=None)
            self.bot.answer_callback_query(call.id, "Пользователь добавлен")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

        except Exception as e:
            self.logger.exception(f"Error in handle_set_role_for_contact_callback: {e}")
            self.bot.edit_message_text(f"Произошла непредвиденная ошибка: {e}", chat_id, message_id, reply_markup=None)
            self.bot.answer_callback_query(call.id, "Ошибка")
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
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_deactivate}"))
        sent_msg = self.bot.send_message(chat_id, f"Вы уверены, что хотите деактивировать пользователя ID {user_id_to_deactivate}?", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_deactivation_confirmation_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        user_id_to_deactivate = call.data.split("_")[-1]

        self.logger.info(f"Chat {chat_id}: Deactivating user ID {user_id_to_deactivate}")
        response_message = deactivate_user_in_db(user_id_to_deactivate)
        
        self.bot.edit_message_text(text=response_message, chat_id=chat_id, message_id=message_id, reply_markup=None)
        self.bot.answer_callback_query(call.id, text=response_message)
        self.finish_um_operation_and_return_to_staff_menu(chat_id)

    def cancel_um_operation_and_return_to_staff_menu(self, chat_id, message_text="Операция с персоналом отменена."):
        self.logger.info(f"Chat {chat_id}: User management operation cancelled. Returning to staff menu.")
        current_state = global_main_user_state.get(chat_id, "")
        if current_state.startswith("UM_") or current_state.startswith("USER_MANAGEMENT"):
            global_main_user_state.pop(chat_id, None)
        self.current_operation_data.pop(chat_id, None) 
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
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_select_user_") or \
                                                           call.data == "um_cancel_inline_to_staff_menu")
        def route_user_selection_callback(call):
            if call.data == "um_cancel_inline_to_staff_menu":
                 self.bot.delete_message(call.message.chat.id, call.message.message_id)
                 self.finish_um_operation_and_return_to_staff_menu(call.message.chat.id, "Операция отменена.")
            else:
                self.handle_user_selection_callback(call)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_action_"))
        def route_user_action_callback(call):
            self.handle_user_action_callback(call)

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_setrole_"))
        def route_set_new_role_callback(call):
            # --- РАЗДЕЛЯЕМ ОБРАБОТКУ ---
            # Это для добавления по контакту
            if call.data.startswith("um_setrole_contact_"):
                self.handle_set_role_for_contact_callback(call)
            # Это для существующей логики изменения роли
            else:
                self.handle_set_new_role_callback(call)
        
        # --- НОВЫЙ ОТДЕЛЬНЫЙ ОБРАБОТЧИК ДЛЯ ОТМЕНЫ ДОБАВЛЕНИЯ ПО КОНТАКТУ ---
        @self.bot.callback_query_handler(func=lambda call: call.data == "um_cancel_contact_add")
        def route_cancel_contact_add(call):
             self.handle_set_role_for_contact_callback(call) # Логика отмены уже внутри этой функции

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_confirm_deactivate_"))
        def route_deactivation_confirm_callback(call):
            self.handle_deactivation_confirmation_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers()
    um_instance.register_callback_handlers()
    return um_instance