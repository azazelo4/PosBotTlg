"""Навигация: отмена, назад, разделы меню и ответ «не понял». Регистрируются первыми/последними."""
from telebot import types

from core.auth import has_role
from handlers.buttons import (create_cash_operations_buttons, create_stock_management_buttons, create_staff_management_buttons,
                     get_keyboard_by_role, create_share_contact_markup)
from core.state import bot, user_state, cancel_current_operation


def on_cancel(message: types.Message):
    chat_id = message.chat.id
    if not cancel_current_operation(chat_id, notify=True):
        bot.send_message(chat_id, "Нет активной операции.", reply_markup=get_keyboard_by_role(chat_id))


def on_back(message: types.Message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    bot.send_message(chat_id, "Главное меню.", reply_markup=get_keyboard_by_role(chat_id))


def on_cash_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Кассовые операции:", reply_markup=create_cash_operations_buttons())


def on_stock_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Управление складом:", reply_markup=create_stock_management_buttons())


def on_staff_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Управление персоналом:", reply_markup=create_staff_management_buttons())


def on_fallback(message: types.Message):
    """Последний хендлер: ничего не подошло."""
    chat_id = message.chat.id
    if not has_role(chat_id):
        bot.send_message(chat_id, "Чтобы начать работу, поделитесь номером телефона.", reply_markup=create_share_contact_markup())
        return
    if user_state.get(chat_id):
        # Сообщение не того типа (фото, стикер) посреди операции
        bot.send_message(chat_id, "Ожидаю текстовый ответ. Чтобы прервать операцию, нажмите «Отмена».")
        return
    bot.send_message(chat_id, "Не понял команду. Выберите действие в меню.", reply_markup=get_keyboard_by_role(chat_id))


def on_stale_callback(call: types.CallbackQuery):
    bot.answer_callback_query(call.id, "Кнопка устарела.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
