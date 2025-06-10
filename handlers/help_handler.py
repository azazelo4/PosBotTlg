from telebot import types
from state import bot # Import the shared bot instance
import logging # Import the logging module

# No local bot instance here
logger = logging.getLogger(__name__) # Define the logger instance

def help_command_handler(message: types.Message):
    help_text = """
Доступные команды:
/start - начать диалог с ботом, авторизация по номеру телефона.
/help - показать это сообщение.

Основные функции:
- **Касса**: Доступ к операциям продаж, возвратов, скупки, Trade-In и отчетов.
  - **Продажа**: Поиск товара по артикулу, ввод суммы, выбор оплаты, подтверждение.
  - **Возврат**: Поиск заказа по ID, ввод суммы возврата, выбор оплаты, подтверждение.
  - **Скупка**: Поиск товара по артикулу, ввод цены скупки, выбор оплаты, подтверждение.
  - **Trade-In**: Ввод артикулов старого и нового товаров, ввод доплаты, выбор оплаты доплаты, подтверждение.
  - **Отчеты**: Генерация отчетов по продажам (ежедневный, еженедельный, ежемесячный, кастомный период).
- **Персонал** (для администраторов):
  - **Добавить пользователя**: Добавление нового пользователя в систему.
  - **Удалить пользователя**: Деактивация пользователя.
  - **Права**: Изменение ролей пользователей.

Навигация:
- Кнопка **"Назад"**: Возврат в предыдущее меню или главное меню.
- Кнопка **"Отмена"**: Отмена текущей многошаговой операции (например, продажи или отчета).

Если что-то пошло не так, попробуйте команду /start для перезапуска процесса авторизации.
"""
    bot.send_message(message.chat.id, help_text, reply_markup=types.ReplyKeyboardRemove()) # Remove keyboard if any

def register_help_handlers():
    # Note: bot is imported from state.py
    bot.register_message_handler(help_command_handler, commands=['help'], pass_bot=False)
    logger.info("Help handler registered.")