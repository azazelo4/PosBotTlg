"""Управление персоналом (только администратор)."""
import logging

from telebot import types

from auth import has_role, invalidate_user_sessions
from buttons import create_cancel_markup, create_choice_markup, create_staff_management_buttons, ROLE_TITLES
from database import (add_user_to_db, get_all_users, get_user_by_id, change_user_role_function, update_user_phone,
                      deactivate_user_in_db)
from handlers.common import in_state, is_button
from state import bot, user_data, user_state, get_op, start_op, end_op, register_cancel, cancel_current_operation
from utils.common import normalize_phone, h

logger = logging.getLogger(__name__)

ADD_NAME, ADD_PHONE, ADD_ROLE, EDIT_PHONE = "UM_ADD_NAME", "UM_ADD_PHONE", "UM_ADD_ROLE", "UM_EDIT_PHONE"
ROLE_BY_TITLE = {"Кассир": 'cashier', "Менеджер": 'manager', "Администратор": 'admin'}


def staff_menu(chat_id, text):
    bot.send_message(chat_id, text, reply_markup=create_staff_management_buttons())


def _cancel(chat_id, notify):
    end_op(chat_id)
    if notify:
        staff_menu(chat_id, "Операция с персоналом отменена.")


# ------------------------------------------------------------ добавление

def start_add(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, ADD_NAME)
    bot.send_message(chat_id, "Новый сотрудник. Имя и фамилия:", reply_markup=create_cancel_markup())


def on_add_name(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if len(text) < 2:
        bot.send_message(chat_id, "Введите имя сотрудника.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['name'] = text[:255]
    user_state[chat_id] = ADD_PHONE
    bot.send_message(chat_id, "Телефон сотрудника (+7XXXXXXXXXX или 8XXXXXXXXXX). Именно с этого номера он войдёт в бот:",
                     reply_markup=create_cancel_markup())


def on_add_phone(message):
    chat_id = message.chat.id
    phone = normalize_phone(message.text)
    if not phone:
        bot.send_message(chat_id, "Некорректный номер. Пример: +77011234567.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['phone'] = phone
    user_state[chat_id] = ADD_ROLE
    bot.send_message(chat_id, "Роль:", reply_markup=create_choice_markup(list(ROLE_BY_TITLE), 3))


def on_add_role(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    role = ROLE_BY_TITLE.get(text)
    if not role:
        bot.send_message(chat_id, "Выберите роль кнопкой.", reply_markup=create_choice_markup(list(ROLE_BY_TITLE), 3))
        return
    op = get_op(chat_id)
    store_id = (user_data.get(chat_id) or {}).get('store_id')
    ok, msg = add_user_to_db(op['phone'], role, op['name'], store_id)
    end_op(chat_id)
    if ok:
        msg += f"\nТелефон: {op['phone']}. Сотруднику нужно открыть бота и нажать /start."
    staff_menu(chat_id, ("✅ " if ok else "❌ ") + msg)


# ------------------------------------------------------------ права

def _user_title(u):
    name = " ".join(p for p in (u.get('name'), u.get('surename')) if p)
    return f"{name} · {ROLE_TITLES.get(u.get('role'), u.get('role'))} · {u.get('phone_number')}"


def show_users(chat_id, message_id=None):
    users = get_all_users()
    if users is None:
        staff_menu(chat_id, "❌ Ошибка базы данных.")
        return
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for u in users:
        keyboard.add(types.InlineKeyboardButton(_user_title(u), callback_data=f"um_user_{u['id']}"))
    keyboard.add(types.InlineKeyboardButton("Закрыть", callback_data="um_close"))
    text = "Выберите сотрудника:" if users else "Активных сотрудников нет."
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
    else:
        bot.send_message(chat_id, text, reply_markup=keyboard)


def start_rights(message):
    cancel_current_operation(message.chat.id, notify=False)
    show_users(message.chat.id)


def _actions_keyboard(user_id, is_self):
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not is_self:
        kb.add(types.InlineKeyboardButton("Изменить роль", callback_data=f"um_role_{user_id}"))
    kb.add(types.InlineKeyboardButton("Изменить телефон", callback_data=f"um_phone_{user_id}"))
    if not is_self:
        kb.add(types.InlineKeyboardButton("Деактивировать", callback_data=f"um_deact_{user_id}"))
    kb.add(types.InlineKeyboardButton("« К списку", callback_data="um_list"))
    return kb


def on_callback(call):
    chat_id, message_id = call.message.chat.id, call.message.message_id
    if not has_role(chat_id, 'admin'):
        bot.answer_callback_query(call.id, "Доступно только администратору.")
        return
    bot.answer_callback_query(call.id)
    data = call.data
    me = (user_data.get(chat_id) or {}).get('id')

    if data == "um_close":
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        return
    if data == "um_list":
        show_users(chat_id, message_id)
        return

    action, _, rest = data[3:].partition('_')
    user_id_str, _, extra = rest.partition('_')
    user_id = int(user_id_str)
    user = get_user_by_id(user_id)
    if not user or not user.get('is_active'):
        bot.edit_message_text("Сотрудник не найден или уже деактивирован.", chat_id, message_id)
        return
    is_self = user_id == me
    title = h(_user_title(user))

    if action == "user":
        bot.edit_message_text(f"<b>{title}</b>\nЧто сделать?", chat_id, message_id, parse_mode='HTML',
                              reply_markup=_actions_keyboard(user_id, is_self))
    elif action == "role" and not is_self:
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(*[types.InlineKeyboardButton(t, callback_data=f"um_setrole_{user_id}_{r}") for t, r in ROLE_BY_TITLE.items()])
        kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"um_user_{user_id}"))
        bot.edit_message_text(f"<b>{title}</b>\nНовая роль:", chat_id, message_id, parse_mode='HTML', reply_markup=kb)
    elif action == "setrole" and not is_self:
        ok, msg = change_user_role_function(user_id, extra)
        invalidate_user_sessions(user_id)
        bot.edit_message_text(("✅ " if ok else "❌ ") + f"{h(user['name'])}: {h(msg)}", chat_id, message_id)
    elif action == "deact" and not is_self:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("Да, деактивировать", callback_data=f"um_deactok_{user_id}"))
        kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"um_user_{user_id}"))
        bot.edit_message_text(f"Деактивировать <b>{title}</b>? Сотрудник потеряет доступ к боту.", chat_id, message_id,
                              parse_mode='HTML', reply_markup=kb)
    elif action == "deactok" and not is_self:
        ok, msg = deactivate_user_in_db(user_id)
        invalidate_user_sessions(user_id)
        bot.edit_message_text(("✅ " if ok else "❌ ") + f"{h(user['name'])}: {h(msg)}", chat_id, message_id)
    elif action == "phone":
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        cancel_current_operation(chat_id, notify=False)
        start_op(chat_id, EDIT_PHONE)
        get_op(chat_id)['user'] = user
        bot.send_message(chat_id, f"Новый телефон для {h(user['name'])} (+7XXXXXXXXXX):", reply_markup=create_cancel_markup())
    else:
        bot.edit_message_text("Нельзя изменить собственную роль или деактивировать себя.", chat_id, message_id)


def on_edit_phone(message):
    chat_id = message.chat.id
    phone = normalize_phone(message.text)
    if not phone:
        bot.send_message(chat_id, "Некорректный номер. Пример: +77011234567.", reply_markup=create_cancel_markup())
        return
    user = get_op(chat_id)['user']
    ok, msg = update_user_phone(user['id'], phone)
    end_op(chat_id)
    if ok and user['id'] != (user_data.get(chat_id) or {}).get('id'):
        invalidate_user_sessions(user['id'])
        msg += " Сотруднику нужно заново войти через /start."
    staff_menu(chat_id, ("✅ " if ok else "❌ ") + msg)


def configure_user_management():
    register_cancel("UM_", _cancel)
    bot.register_message_handler(start_add, func=is_button("Добавить пользователя", 'admin'))
    bot.register_message_handler(start_rights, func=is_button("Права", 'admin'))
    bot.register_message_handler(on_add_name, func=in_state(ADD_NAME))
    bot.register_message_handler(on_add_phone, func=in_state(ADD_PHONE))
    bot.register_message_handler(on_add_role, func=in_state(ADD_ROLE))
    bot.register_message_handler(on_edit_phone, func=in_state(EDIT_PHONE))
    bot.register_callback_query_handler(on_callback, func=lambda c: c.data.startswith("um_"))
