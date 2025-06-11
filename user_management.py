from telebot import types
from database import add_user_to_db, deactivate_user_in_db, update_user_phone, get_user_by_phone
import logging
import re # Для нормализации номера
from state import user_state as global_main_user_state, user_data as global_main_user_data 
from buttons import create_staff_management_buttons, create_cancel_markup

logger = logging.getLogger(__name__)

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
                    self._send_user_action_menu_as_new(chat_id, user_id_being_edited, "Изменение телефона отменено.")
                else:
                    self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена.")
                return

            normalized_new_phone = normalize_phone_number(new_phone_text_raw)
            if not normalized_new_phone:
                self.bot.send_message(chat_id, f"Не удалось распознать номер телефона: {new_phone_text_raw}. Введите корректный номер (например, +77001234567) или 'Отмена'.", reply_markup=create_cancel_markup())
                return

            if not user_id_being_edited:
                self.logger.error(f"Chat {chat_id}: State is {UM_STATE_AWAITING_NEW_PHONE_FOR_USER} but no user_id_to_edit found.")
                self.cancel_um_operation_and_return_to_staff_menu(chat_id, "Произошла ошибка, операция отменена.")
                return
            
            success, db_message = update_user_phone(user_id_being_edited, normalized_new_phone) # Передаем нормализованный номер
            self.bot.send_message(chat_id, db_message, reply_markup=create_staff_management_buttons()) 
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
            keyboard.add(types.InlineKeyboardButton(text="Отмена (в меню персонала)", callback_data="um_cancel_inline_to_staff_menu"))
            sent_message = self.bot.send_message(chat_id, "Выберите пользователя для управления:", reply_markup=keyboard)
            self.current_operation_data[chat_id] = {'inline_message_id': sent_message.message_id} 
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
        self.current_operation_data[chat_id]['inline_message_id'] = sent_msg.message_id


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
            self.bot.answer_callback_query(call.id, "Ошибка")
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
            self.finish_um_operation_and_return_to_staff_menu(chat_id) # Возврат в меню если действие непонятно
            return # Явно выходим, чтобы не выполнить answer_callback_query еще раз
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
        keyboard.add(types.InlineKeyboardButton(text="< Назад (к действиям)", callback_data=f"um_select_user_{user_id_to_edit}")) # Используем um_select_user для возврата к меню действий для этого юзера
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


    def _prompt_for_new_phone(self, chat_id, user_id_to_edit):
        global_main_user_state[chat_id] = UM_STATE_AWAITING_NEW_PHONE_FOR_USER
        # Убедимся, что user_id_to_edit есть в current_operation_data
        if chat_id not in self.current_operation_data: self.current_operation_data[chat_id] = {}
        self.current_operation_data[chat_id].update({'action': 'change_phone', 'user_id_to_edit': user_id_to_edit})
        self.bot.send_message(chat_id, f"Введите новый номер телефона для пользователя ID {user_id_to_edit} (например, +77001234567 или 87001234567) или 'Отмена':", reply_markup=create_cancel_markup())

    def _prompt_for_deactivation_confirm(self, chat_id, user_id_to_deactivate):
        global_main_user_state[chat_id] = 'USER_MANAGEMENT_DEACTIVATE_CONFIRM' 
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
        
        # Не отправляем сообщение если оно уже было отправлено (например, сообщение об ошибке или успехе)
        # Это предотвратит дублирование "Выберите следующее действие..." если оно не нужно.
        # Однако, если message_text не задан, то дефолтное сообщение отправим.
        if message_text:
            self.bot.send_message(chat_id, message_text, reply_markup=create_staff_management_buttons())
        elif not any(key.startswith("UM_") or key.startswith("USER_MANAGEMENT") for key in [global_main_user_state.get(chat_id,"")]): # Отправляем если нет активного стейта
            self.bot.send_message(chat_id, "Выберите следующее действие в меню персонала:", reply_markup=create_staff_management_buttons())


    def register_callback_handlers(self):
        # um_select_user_ID (выбор пользователя из списка, показывает меню действий)
        # um_cancel_inline_to_staff_menu (отмена из inline клавиатуры в меню персонала)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_select_user_") or \
                                                           call.data == "um_cancel_inline_to_staff_menu")
        def route_user_selection_callback(call):
            if call.data == "um_cancel_inline_to_staff_menu":
                 self.bot.delete_message(call.message.chat.id, call.message.message_id)
                 self.finish_um_operation_and_return_to_staff_menu(call.message.chat.id, "Операция отменена.")
            else:
                self.handle_user_selection_callback(call)

        # um_action_ACTION_ID (выбор действия для пользователя)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_action_"))
        def route_user_action_callback(call):
            self.handle_user_action_callback(call)

        # um_setrole_ID_ROLE (подтверждение новой роли)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_setrole_"))
        def route_set_new_role_callback(call):
            self.handle_set_new_role_callback(call)
        
        # um_confirm_deactivate_ID (подтверждение деактивации)
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("um_confirm_deactivate_"))
        def route_deactivation_confirm_callback(call):
            self.handle_deactivation_confirmation_callback(call)


def configure_user_management(bot_instance, global_user_data_ref, get_users_func, change_role_func):
    um_instance = UserManagement(bot_instance, global_user_data_ref, get_users_func, change_role_func)
    um_instance.configure_handlers()
    um_instance.register_callback_handlers()
    return um_instance