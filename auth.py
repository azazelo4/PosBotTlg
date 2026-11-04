"""Авторизация и сессии пользователей.

Сессия (user_data[chat_id]) живёт в памяти, но chat_id сохраняется в БД при входе,
поэтому после перезапуска бота пользователю не нужно заново делиться номером.
Каждые SESSION_TTL секунд роль и активность перечитываются из БД — так
деактивация и смена роли администратором применяются сразу.
"""
import logging
import time

from telebot.handler_backends import BaseMiddleware

from database import get_user_by_phone, get_user_by_chat_id, get_user_by_id, set_user_chat_id
from state import user_data, user_state

logger = logging.getLogger(__name__)

SESSION_TTL = 300
ROLES = ('admin', 'manager', 'cashier')


def authorize_user(phone_number):
    """Возвращает (user, None) или (None, текст ошибки)."""
    try:
        user = get_user_by_phone(phone_number)
    except Exception:
        logger.exception("Ошибка при авторизации")
        return None, "Произошла ошибка при авторизации. Попробуйте ещё раз."
    if user is None:
        return None, "Пользователь с этим номером не найден. Обратитесь к администратору."
    if not user.get('is_active'):
        return None, "Ваш аккаунт деактивирован. Обратитесь к администратору."
    if user.get('role') not in ROLES:
        return None, "Для вашей роли доступ к боту не настроен. Обратитесь к администратору."
    return user, None


def load_session(chat_id, db_user, keep_op=True):
    old = user_data.get(chat_id, {})
    user_data[chat_id] = {
        'id': db_user['id'],
        'store_id': db_user.get('store_id'),
        'name': db_user.get('name') or '',
        'surename': db_user.get('surename') or '',
        'role': db_user.get('role'),
        'phone_number': db_user.get('phone_number'),
        'is_active': 1 if db_user.get('is_active') else 0,
        'checked_at': time.monotonic(),
    }
    if keep_op and 'op' in old:
        user_data[chat_id]['op'] = old['op']


def login(chat_id, db_user):
    load_session(chat_id, db_user, keep_op=False)
    user_state.pop(chat_id, None)
    ok, msg = set_user_chat_id(db_user['id'], chat_id)
    if not ok:
        logger.warning("Chat %s: не удалось сохранить chat_id: %s", chat_id, msg)


def drop_session(chat_id):
    user_data.pop(chat_id, None)
    user_state.pop(chat_id, None)


def ensure_session(chat_id):
    """Восстанавливает/обновляет сессию из БД. Возвращает данные сессии или None."""
    session = user_data.get(chat_id)
    if session and session.get('id') and time.monotonic() - session.get('checked_at', 0) < SESSION_TTL:
        return session
    try:
        db_user = get_user_by_id(session['id']) if session and session.get('id') else get_user_by_chat_id(chat_id)
    except Exception:
        logger.exception("Chat %s: ошибка проверки сессии", chat_id)
        return session
    if db_user is None:
        # Пользователей не удаляем, поэтому None для уже вошедшего — это сбой БД:
        # не выкидываем его из сессии, проверим в следующий раз
        if session and session.get('id'):
            return session
        return None
    if not db_user.get('is_active') or db_user.get('role') not in ROLES or (session and db_user.get('chat_id') not in (None, chat_id)):
        return _expire(chat_id, session)
    load_session(chat_id, db_user)
    return user_data[chat_id]


def _expire(chat_id, session):
    if session and session.get('id'):
        logger.info("Chat %s: сессия пользователя %s больше не действительна", chat_id, session.get('id'))
    drop_session(chat_id)
    return None


def invalidate_user_sessions(user_id):
    """После смены роли/телефона/деактивации — перечитать сессии этого пользователя."""
    for chat_id, session in list(user_data.items()):
        if session.get('id') == int(user_id):
            session['checked_at'] = 0


def has_role(chat_id, *roles):
    session = user_data.get(chat_id) or {}
    return bool(session.get('is_active')) and session.get('role') in (roles or ROLES)


class SessionMiddleware(BaseMiddleware):
    """Перед обработкой любого сообщения/нажатия восстанавливает сессию из БД."""

    def __init__(self):
        super().__init__()
        self.update_sensitive = False
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        chat = getattr(update, 'chat', None) or getattr(getattr(update, 'message', None), 'chat', None)
        if chat is None:
            return
        # Не мешаем /start и передаче контакта — это и есть вход
        if getattr(update, 'content_type', None) == 'contact':
            return
        ensure_session(chat.id)

    def post_process(self, update, data, exception=None):
        pass
