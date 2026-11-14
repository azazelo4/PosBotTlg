# core/state.py — общий экземпляр бота и состояние диалогов
import logging

from telebot import TeleBot

from core import config

logger = logging.getLogger(__name__)

# use_class_middlewares нужен для восстановления сессии до проверки фильтров хендлеров
# Без токена (до мастера /setup) main.py сюда не доходит; заглушка нужна только для импорта в тестах/утилитах.
bot = TeleBot(config.TOKEN or "0:not-configured", use_class_middlewares=True, validate_token=False)

user_data = {}    # chat_id -> {'id', 'role', 'name', 'surename', 'store_id', 'phone_number', 'is_active', ...}
user_state = {}   # chat_id -> "ПРЕФИКС_ШАГ" текущей многошаговой операции

# Функции отмены операций по префиксу состояния: prefix -> fn(chat_id, notify: bool)
_cancel_handlers = {}


def register_cancel(prefix, fn):
    _cancel_handlers[prefix] = fn


def cancel_current_operation(chat_id, notify=True) -> bool:
    """Отменяет текущую операцию пользователя. True, если была что отменять."""
    current = user_state.get(chat_id)
    if not current:
        return False
    for prefix, fn in _cancel_handlers.items():
        if current.startswith(prefix):
            try:
                fn(chat_id, notify)
            except Exception:
                logger.exception("Chat %s: ошибка при отмене операции %s", chat_id, current)
            break
    # Подстраховка: состояние должно быть сброшено в любом случае
    user_state.pop(chat_id, None)
    if chat_id in user_data:
        user_data[chat_id].pop('op', None)
    return True


def get_op(chat_id) -> dict:
    """Данные текущей операции пользователя."""
    return user_data.setdefault(chat_id, {}).setdefault('op', {})


def start_op(chat_id, state):
    user_data.setdefault(chat_id, {})['op'] = {}
    user_state[chat_id] = state


def end_op(chat_id):
    user_state.pop(chat_id, None)
    if chat_id in user_data:
        user_data[chat_id].pop('op', None)
