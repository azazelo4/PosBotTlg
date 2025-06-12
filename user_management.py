from telebot import types
<<<<<<< HEAD
<<<<<<< HEAD
from database import add_user_to_db, deactivate_user_in_db, update_user_phone, get_user_by_phone
import logging
import re # Для нормализации номера
=======
from database import add_user_to_db, deactivate_user_in_db, update_user_phone, get_user_by_phone # get_user_by_phone needed
import logging
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
from state import user_state as global_main_user_state, user_data as global_main_user_data 
=======
from database import add_user_to_db, deactivate_user_in_db, update_user_phone # Added update_user_phone
import logging
from state import user_state as global_main_user_state, user_data as global_main_user_data # Use the main user_state for FSM
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
from buttons import create_staff_management_buttons, create_cancel_markup

logger = logging.getLogger(__name__)

<<<<<<< HEAD
# Состояния FSM для User Management
UM_STATE_AWAITING_ADD_USER_DETAILS_OR_CONTACT = "UM_AWAITING_ADD_USER_DETAILS_OR_CONTACT" # Объединяем состояния
UM_STATE_AWAITING_ROLE_FOR_CONTACT_USER = "UM_AWAITING_ROLE_FOR_CONTACT_USER"
UM_STATE_AWAITING_NEW_PHONE_FOR_USER = "UM_AWAITING_NEW_PHONE_FOR_USER"
# UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION для inline (уже есть)

def normalize_phone_number(phone_str: str) -> str | None:
    """
    Нормализует номер телефона к формату +7XXXXXXXXXX.
    Удаляет все нецифровые символы, кроме начального '+'.
    Обрабатывает номера, начинающиеся с '7', '8' (для России/Казахстана).
    """
    if not phone_str:
        return None
    
    # Удаляем все, кроме цифр и возможного первого '+'
    cleaned_phone = re.sub(r'[^\d+]', '', phone_str)
    
    if not cleaned_phone:
        return None

    # Если начинается с '+' и далее не '+7', проверяем
    if cleaned_phone.startswith('+'):
        if cleaned_phone.startswith('+7') and len(cleaned_phone) == 12:
            if cleaned_phone[1:].isdigit(): return cleaned_phone
        # Можно добавить другие префиксы, если нужно, например, +380 для Украины
        # Но для текущей задачи фокусируемся на +7
        elif cleaned_phone.startswith('+8') and len(cleaned_phone) == 12 and cleaned_phone[2:].isdigit(): # Редкий случай, но может быть
             return '+7' + cleaned_phone[2:]
        else: # Если +ДРУГОЕ_ЧИСЛО, и это не наш формат, пока считаем невалидным для авто-нормализации к +7
            # Если это уже валидный международный номер другой страны, его можно оставить как есть,
            # но тогда get_user_by_phone и add_user_to_db должны быть готовы к этому.
            # Для простоты, если не +7, то считаем ошибкой для данной задачи.
            if len(cleaned_phone) > 1 and cleaned_phone[1:].isdigit(): # Проверка, что после + идут цифры
                 # Если мы хотим поддерживать любые номера, начинающиеся с +, то возвращаем как есть, если они валидны
                 # return cleaned_phone 
                 # Но так как в задаче "пусть записывается номер как +7xxxxxxxxxx", то другие форматы не подходят
                 pass # Пропускаем, чтобы попасть в проверки ниже или вернуть None
    
    # Убираем начальный '+' для дальнейших проверок, если он был, но не прошел верхние условия
    if cleaned_phone.startswith('+'):
        temp_phone = cleaned_phone[1:]
    else:
        temp_phone = cleaned_phone

    if len(temp_phone) == 11 and temp_phone.startswith('8') and temp_phone.isdigit():
        return '+7' + temp_phone[1:]
    elif len(temp_phone) == 11 and temp_phone.startswith('7') and temp_phone.isdigit():
        return '+7' + temp_phone
    elif len(temp_phone) == 10 and temp_phone.isdigit(): # Предполагаем, что это номер без '7' или '8'
        return '+7' + temp_phone
        
    # Если после всех попыток не получили +7XXXXXXXXXX
    if cleaned_phone.startswith('+7') and len(cleaned_phone) == 12 and cleaned_phone[1:].isdigit():
        return cleaned_phone # Возвращаем, если это уже был +7 в правильном формате

    logger.warning(f"Не удалось нормализовать номер: {phone_str} -> {cleaned_phone}")
    return None

=======
UM_STATE_AWAITING_ADD_USER_DETAILS = "UM_AWAITING_ADD_USER_DETAILS"
UM_STATE_AWAITING_DELETE_USER_PHONE = "UM_AWAITING_DELETE_USER_PHONE" # Not used if deactivating by ID from inline menu
UM_STATE_AWAITING_NEW_PHONE_FOR_USER = "UM_AWAITING_NEW_PHONE_FOR_USER"
<<<<<<< HEAD
# UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION for inline deactivation confirm
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
UM_STATE_AWAITING_DEACTIVATE_CONFIRMATION = "UM_AWAITING_DEACTIVATE_CONFIRMATION"

# USER_MANAGEMENT state (for inline keyboard) is set by handle_role_management
# UserManagement class now handles the states after a user is selected from "Права"
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

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
<<<<<<< HEAD
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ADD_USER_DETAILS_OR_CONTACT
            admin_store_id = self.global_user_data.get(chat_id, {}).get('store_id')
            self.current_operation_data[chat_id] = {'admin_store_id': admin_store_id}
            self.bot.send_message(chat_id, 
                                  "Чтобы добавить пользователя, введите его данные в формате:\n"
                                  "<b>Имя Фамилия, +7XXXXXXXXXX, Роль (admin/cashier/manager)</b>\n\n"
                                  "Или просто <b>отправьте контакт</b> пользователя (кнопка 'скрепка' -> 'Контакт'). "
                                  "После этого я запрошу роль.",
                                  parse_mode="HTML",
                                  reply_markup=create_cancel_markup())

        # Обработчик для ручного ввода данных
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS_OR_CONTACT and message.content_type == 'text')
        def process_add_user_manual_data_handler(message):
            chat_id = message.chat.id
            text = message.text.strip()
            if text == "Отмена": 
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление отменено.")
                return
            try:
                parts = text.split(",", 2) 
                if len(parts) != 3: 
                    self.bot.send_message(chat_id, "Неверный формат. Ожидается: Имя Фамилия, Телефон, Роль.\nПопробуйте снова или отправьте контакт.", reply_markup=create_cancel_markup())
                    return
                
                name_surname, phone_raw, role_text = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()
                
                normalized_phone = normalize_phone_number(phone_raw)
                if not normalized_phone:
                    self.bot.send_message(chat_id, f"Не удалось распознать номер телефона: {phone_raw}. Введите корректный номер (например, +77001234567 или 87001234567).", reply_markup=create_cancel_markup())
                    return

                if role_text not in ['admin', 'cashier', 'manager']:
                    self.bot.send_message(chat_id, "Роль должна быть 'admin', 'cashier' или 'manager'.", reply_markup=create_cancel_markup())
                    return

                admin_store_id = self.current_operation_data.get(chat_id, {}).get('admin_store_id', 1) 
                
                response_msg = add_user_to_db(normalized_phone, role_text, name_surname, admin_store_id)
                self.bot.send_message(chat_id, response_msg)
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
            except Exception as e: 
                self.logger.exception(f"Chat {chat_id}: Error adding user manually.")
                self.bot.send_message(chat_id, f"Ошибка добавления: {e}")
                self.finish_um_operation_and_return_to_staff_menu(chat_id)

        # Обработчик для получения контакта
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ADD_USER_DETAILS_OR_CONTACT and message.content_type == 'contact')
        def process_add_user_contact_handler(message):
            chat_id = message.chat.id
            contact = message.contact
            self.logger.info(f"Chat {chat_id}: Received contact for adding user: {contact.phone_number}")

            normalized_phone = normalize_phone_number(contact.phone_number)
            if not normalized_phone:
                self.bot.send_message(chat_id, f"Не удалось корректно обработать номер из контакта: {contact.phone_number}. Попробуйте ввести данные вручную или другой контакт.", reply_markup=create_cancel_markup())
                return

            # Проверим, существует ли уже пользователь с таким номером
            existing_user = get_user_by_phone(normalized_phone) # get_user_by_phone должен использовать нормализацию!
            if existing_user and existing_user.get('is_active'):
                self.bot.send_message(chat_id, f"Пользователь с номером {normalized_phone} ({existing_user.get('name', '')}) уже существует и активен. Добавление отменено.", reply_markup=create_staff_management_buttons())
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
                return
            elif existing_user and not existing_user.get('is_active'):
                 self.bot.send_message(chat_id, f"Пользователь с номером {normalized_phone} ({existing_user.get('name', '')}) существует, но деактивирован. Для активации и смены роли используйте меню 'Права'. Добавление отменено.", reply_markup=create_staff_management_buttons())
                 self.finish_um_operation_and_return_to_staff_menu(chat_id)
                 return


            name_from_contact = contact.first_name
            if contact.last_name:
                name_from_contact += f" {contact.last_name}"
            
            # Сохраняем промежуточные данные
            op_data = self.current_operation_data.get(chat_id, {})
            op_data.update({
                'contact_phone': normalized_phone,
                'contact_name': name_from_contact.strip()
            })
            self.current_operation_data[chat_id] = op_data
            
            global_main_user_state[chat_id] = UM_STATE_AWAITING_ROLE_FOR_CONTACT_USER
            self.bot.send_message(chat_id, 
                                  f"Контакт получен:\nИмя: {name_from_contact.strip()}\nТелефон: {normalized_phone}\n\n"
                                  "Теперь введите <b>роль</b> для этого пользователя (admin/cashier/manager):",
                                  parse_mode="HTML",
                                  reply_markup=create_cancel_markup())

        # Обработчик для ввода роли после отправки контакта
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_ROLE_FOR_CONTACT_USER and message.content_type == 'text')
        def process_role_for_contact_user_handler(message):
            chat_id = message.chat.id
            role_text = message.text.strip().lower()

            if role_text == "отмена": # "Отмена" должна быть с маленькой буквы, как на кнопке
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Добавление отменено.")
                return

            if role_text not in ['admin', 'cashier', 'manager']:
                self.bot.send_message(chat_id, "Роль должна быть 'admin', 'cashier' или 'manager'. Введите снова или 'Отмена'.", reply_markup=create_cancel_markup())
                return

            op_data = self.current_operation_data.get(chat_id)
            if not op_data or 'contact_phone' not in op_data or 'contact_name' not in op_data:
                self.logger.error(f"Chat {chat_id}: Missing contact_phone or contact_name in operation_data while awaiting role.")
                self.bot.send_message(chat_id, "Произошла ошибка, данные контакта утеряны. Попробуйте добавить пользователя заново.", reply_markup=create_staff_management_buttons())
                self.finish_um_operation_and_return_to_staff_menu(chat_id)
                return
            
            phone_to_add = op_data['contact_phone']
            name_to_add = op_data['contact_name']
            admin_store_id = op_data.get('admin_store_id', 1)

            response_msg = add_user_to_db(phone_to_add, role_text, name_to_add, admin_store_id)
            self.bot.send_message(chat_id, response_msg)
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
        
        @self.bot.message_handler(func=lambda message: global_main_user_state.get(message.chat.id) == UM_STATE_AWAITING_NEW_PHONE_FOR_USER)
        def process_new_phone_input(message: types.Message):
            chat_id = message.chat.id
            new_phone_text_raw = message.text.strip()
            operation_data = self.current_operation_data.get(chat_id, {})
            user_id_being_edited = operation_data.get('user_id_to_edit')

            if new_phone_text_raw.lower() == "отмена": # Сверяем с кнопкой
                global_main_user_state.pop(chat_id, None) 
                if user_id_being_edited:
                    self.bot.delete_message(chat_id, message.message_id) 
=======
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
<<<<<<< HEAD
                global_main_user_state.pop(chat_id, None) # Clear current state
                if user_id_being_edited and original_inline_message_id:
                    # Re-send the action menu for the user by editing the original message
                    # This requires storing and retrieving the original_inline_message_id correctly.
                    # For simplicity, we might just send a new action menu or go back to user list.
                    self.bot.delete_message(chat_id, message.message_id) # Delete "Отмена" message
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                    self._send_user_action_menu_as_new(chat_id, user_id_being_edited, "Изменение телефона отменено.")
                else:
=======
                # Go back to user action selection menu, not staff menu
                user_id_being_edited = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
                if user_id_being_edited:
                    self.bot.delete_message(chat_id, message.message_id) # Clean up "Отмена" reply keyboard
                    self._send_user_action_menu(chat_id, user_id_being_edited, "Изменение телефона отменено.")
                else: # Should not happen if state is correct
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                    self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
                return

            normalized_new_phone = normalize_phone_number(new_phone_text_raw)
            if not normalized_new_phone:
                self.bot.send_message(chat_id, f"Не удалось распознать номер телефона: {new_phone_text_raw}. Введите корректный номер (например, +77001234567) или 'Отмена'.", reply_markup=create_cancel_markup())
                return

            user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
            if not user_id_to_edit:
                self.logger.error(f"Chat {chat_id}: State is {UM_STATE_AWAITING_NEW_PHONE_FOR_USER} but no user_id_to_edit found.")
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Произошла ошибка, операция отменена.")
                return
            
<<<<<<< HEAD
<<<<<<< HEAD
            success, db_message = update_user_phone(user_id_being_edited, normalized_new_phone) # Передаем нормализованный номер
            self.bot.send_message(chat_id, db_message, reply_markup=create_staff_management_buttons()) 
=======
            success, db_message = update_user_phone(user_id_being_edited, new_phone_text)
            self.bot.send_message(chat_id, db_message, reply_markup=create_staff_management_buttons()) # ReplyMarkup to remove "Отмена"
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
            success, db_message = update_user_phone(user_id_to_edit, new_phone_text)
            self.bot.send_message(chat_id, db_message)
            # After updating, go back to the staff menu or user action menu? For now, staff menu.
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            self.finish_um_operation_and_return_to_staff_menu(chat_id)


    # --- Methods for "Права" flow (mostly inline keyboards) ---
    def handle_role_management(self, chat_id): # Called by main.py for "Права" button
        self.logger.debug(f"UserManagement instance handling 'Права' for chat_id: {chat_id}")
<<<<<<< HEAD
<<<<<<< HEAD
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_USER_LIST' 
=======
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_USER_LIST' # New state for user list
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        self.current_operation_data.pop(chat_id, None) 
=======
        global_main_user_state[chat_id] = 'USER_MANAGEMENT' # Indicates "Права" flow is active
        self.current_operation_data.pop(chat_id, None) # Clear any previous op data for this chat
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

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
<<<<<<< HEAD
            sent_message = self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
<<<<<<< HEAD
            self.current_operation_data[chat_id] = {'inline_message_id': sent_message.message_id} 
=======
            self.current_operation_data[chat_id] = {'inline_message_id': sent_message.message_id} # Store message ID to edit later
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
            self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        except Exception as e:
            self.logger.exception(f"Error in UserManagement.handle_role_management: {e}")
            self.bot.send_message(chat_id, "Ошибка получения списка пользователей.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)

<<<<<<< HEAD
    def _send_user_action_menu_as_new(self, chat_id, user_id_to_edit, message_text_intro="Выберите действие:"):
        """Sends the user action menu as a new message."""
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ACTION_SELECT'
<<<<<<< HEAD
=======
        # Ensure user_id_to_edit is in current_operation_data or add it
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        if chat_id not in self.current_operation_data: self.current_operation_data[chat_id] = {}
        self.current_operation_data[chat_id]['user_id_to_edit'] = user_id_to_edit
=======
    def _send_user_action_menu(self, chat_id, user_id_to_edit, previous_message_text=None):
        """Sends the menu of actions for a selected user."""
        self.logger.debug(f"Chat {chat_id}: Sending action menu for user ID {user_id_to_edit}")
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ACTION_SELECT' # New state
        self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit}

        # You might want to fetch user's current name/phone to display in the message
        # For now, a generic message
        message_text = previous_message_text or f"Выберите действие для пользователя ID {user_id_to_edit}:"
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Изменить роль", callback_data=f"um_action_changerole_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Изменить телефон", callback_data=f"um_action_changephone_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="Деактивировать", callback_data=f"um_action_deactivate_{user_id_to_edit}"))
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к списку)", callback_data="um_action_back_to_user_list"))
        
<<<<<<< HEAD
        sent_msg = self.bot.send_message(chat_id, message_text_intro, reply_markup=keyboard)
<<<<<<< HEAD
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id
=======
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id # Store new message_id
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
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
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)


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
<<<<<<< HEAD
<<<<<<< HEAD
=======
            # Store user_id and original message_id for potential edits or context
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit, 'inline_message_id': message_id}
=======
            self.current_operation_data[chat_id] = {'user_id_to_edit': user_id_to_edit, 'message_id_for_edit': message_id}
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            
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
<<<<<<< HEAD
<<<<<<< HEAD
        message_id = call.message.message_id
=======
        message_id = call.message.message_id # This is the ID of the action menu message
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        self.logger.debug(f"Chat {chat_id}: User action callback: {action_data}")

        # Extract user_id from self.current_operation_data or from callback
        user_id_to_edit = self.current_operation_data.get(chat_id, {}).get('user_id_to_edit')
<<<<<<< HEAD
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
<<<<<<< HEAD
             try: user_id_to_edit = action_data.split("_")[-1]
             except IndexError: pass 
=======
             try: user_id_to_edit = action_data.split("_")[-1] # Attempt to re-parse if context lost
             except IndexError: pass # Keep it None if parsing fails
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        
        if not user_id_to_edit and not action_data.startswith("um_action_back_to_user_list"):
            self.logger.error(f"Chat {chat_id}: Could not determine user_id for action '{action_data}'. current_op_data: {self.current_operation_data.get(chat_id)}")
            self.bot.edit_message_text("Ошибка: пользователь не определен. Операция прервана.", chat_id, message_id, reply_markup=None)
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
<<<<<<< HEAD
            self.bot.answer_callback_query(call.id, "Ошибка")
            return
        
        self.bot.delete_message(chat_id, message_id) 
=======
            return
        
        self.bot.delete_message(chat_id, message_id) # Delete the action menu message
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
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
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

        if action_data.startswith("um_action_changerole_"):
            self._prompt_for_new_role(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_changephone_"):
            self._prompt_for_new_phone(chat_id, user_id_to_edit)
        elif action_data.startswith("um_action_deactivate_"):
            self._prompt_for_deactivation_confirm(chat_id, user_id_to_edit)
        elif action_data == "um_action_back_to_user_list":
<<<<<<< HEAD
<<<<<<< HEAD
            self.handle_role_management(chat_id) 
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id) # Возврат в меню если действие непонятно
            return # Явно выходим, чтобы не выполнить answer_callback_query еще раз
=======
            self.handle_role_management(chat_id) # Re-display user list (sends a new message)
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        self.bot.answer_callback_query(call.id)
=======
            self.handle_role_management(chat_id) # Re-display user list
        else:
            self.bot.answer_callback_query(call.id, "Неизвестное действие.")
            self.finish_um_operation_and_return_to_staff_menu(chat_id)
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)


    def _prompt_for_new_role(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_ROLE_SELECT' # State for callback
        self.current_operation_data[chat_id]['action'] = 'change_role' # Store current action

        keyboard = types.InlineKeyboardMarkup(row_width=1)
<<<<<<< HEAD
<<<<<<< HEAD
        roles = ['admin', 'cashier', 'manager']
=======
        roles = ['admin', 'cashier', 'manager'] # Added manager
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        roles = ['admin', 'cashier']
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        for role in roles:
            keyboard.add(types.InlineKeyboardButton(
                text=role.capitalize(),
                callback_data=f"um_setrole_{user_id_to_edit}_{role}" # Different prefix for this action
            ))
<<<<<<< HEAD
<<<<<<< HEAD
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}")) # Используем um_select_user для возврата к меню действий для этого юзера
=======
        # Back button should go to the action menu for the *same user*
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}"))
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        sent_msg = self.bot.send_message(chat_id, f"Выберите новую роль для пользователя ID {user_id_to_edit}:", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_set_new_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
<<<<<<< HEAD
        message_id = call.message.message_id 
=======
        message_id = call.message.message_id # message with role options
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        data_parts = call.data.split("_") 
=======
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}")) # Go back to action menu for this user
        self.bot.send_message(chat_id, f"Выберите новую роль для пользователя ID {user_id_to_edit}:", reply_markup=keyboard)

    def handle_set_new_role_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        data_parts = call.data.split("_") # um_setrole_USERID_NEWROLE
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
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
<<<<<<< HEAD
<<<<<<< HEAD
        # Убедимся, что user_id_to_edit есть в current_operation_data
        if chat_id not in self.current_operation_data: self.current_operation_data[chat_id] = {}
        self.current_operation_data[chat_id].update({'action': 'change_phone', 'user_id_to_edit': user_id_to_edit})
        self.bot.send_message(chat_id, f"Введите новый номер телефона для пользователя ID {user_id_to_edit} (например, +77001234567 или 87001234567) или 'Отмена':", reply_markup=create_cancel_markup())

    def _prompt_for_deactivation_confirm(self, chat_id, user_id_to_deactivate):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_DEACTIVATE_CONFIRM' 
=======
        self.current_operation_data[chat_id].update({'action': 'change_phone', 'user_id_to_edit': user_id_to_edit})
        self.bot.send_message(chat_id, f"Введите новый номер телефона для пользователя ID {user_id_to_edit} (формат: +7XXXXXXXXXX) или 'Отмена':", reply_markup=create_cancel_markup())

    def _prompt_for_deactivation_confirm(self, chat_id, user_id_to_deactivate):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_DEACTIVATE_CONFIRM' # Specific state
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        self.current_operation_data[chat_id].update({'action': 'deactivate', 'user_id_to_deactivate': user_id_to_deactivate})

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text="Да, деактивировать", callback_data=f"um_confirm_deactivate_{user_id_to_deactivate}"))
<<<<<<< HEAD
=======
        # Back button should go to the action menu for the *same user*
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_deactivate}"))
        sent_msg = self.bot.send_message(chat_id, f"Вы уверены, что хотите деактивировать пользователя ID {user_id_to_deactivate}?", reply_markup=keyboard)
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


    def handle_deactivation_confirmation_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
<<<<<<< HEAD
        message_id = call.message.message_id 
=======
        message_id = call.message.message_id # message with confirm/cancel buttons
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
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
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
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
        
        # Не отправляем сообщение если оно уже было отправлено (например, сообщение об ошибке или успехе)
        # Это предотвратит дублирование "Выберите следующее действие..." если оно не нужно.
        # Однако, если message_text не задан, то дефолтное сообщение отправим.
        if message_text:
            self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())
        elif not any(key.startswith("UM_") or key.startswith("USER_MANAGEMENT") for key in [global_main_user_state.get(chat_id,"")]): # Отправляем если нет активного стейта
            self.bot.send_message(chat_id, "Выберите следующее действие в меню персонала:", reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self):
<<<<<<< HEAD
<<<<<<< HEAD
        # um_select_user_ID (выбор пользователя из списка, показывает меню действий)
        # um_cancel_inline_to_staff_menu (отмена из inline клавиатуры в меню персонала)
=======
        # um_select_user_ID (selects a user from list, shows action menu)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        # Handles selection of a user from the list (sent by handle_role_management)
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_select_user_") or \
                                                           call.data == "um_cancel_inline_to_staff_menu")
        def route_user_selection_callback(call):
            self.handle_user_selection_callback(call)

<<<<<<< HEAD
<<<<<<< HEAD
        # um_action_ACTION_ID (выбор действия для пользователя)
=======
        # um_action_ACTION_ID (selects an action for the user)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        # Handles selection of an action for the chosen user (Изменить роль, Изменить телефон, etc.)
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_action_"))
        def route_user_action_callback(call):
            self.handle_user_action_callback(call)

<<<<<<< HEAD
<<<<<<< HEAD
        # um_setrole_ID_ROLE (подтверждение новой роли)
=======
        # um_setrole_ID_ROLE (confirms the new role)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        # Handles setting the new role after role is selected from inline keyboard
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_setrole_"))
        def route_set_new_role_callback(call):
            self.handle_set_new_role_callback(call)
        
<<<<<<< HEAD
<<<<<<< HEAD
        # um_confirm_deactivate_ID (подтверждение деактивации)
=======
        # um_confirm_deactivate_ID (confirms deactivation)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        # Handles confirmation of deactivation
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_confirm_deactivate_"))
        def route_deactivation_confirm_callback(call):
            self.handle_deactivation_confirmation_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers()
    um_instance.register_callback_handlers()
    return um_instance