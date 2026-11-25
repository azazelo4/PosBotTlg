from telebot import types

from core import config
from handlers.buttons import get_keyboard_by_role
from core.state import bot, user_data

HELP_TEXT = """<b>Команды</b>
/start — вход по номеру телефона
/help — эта справка
/cancel — отменить текущую операцию

<b>Касса</b> (все роли)
• <b>Продажа</b> — артикул → сумма (не ниже минимальной цены) → оплата → подтверждение.
• <b>Возврат</b> — артикул → выбор продажи из списка → сумма возврата → способ выплаты.
• <b>Скупка</b> — изделие нашего магазина по артикулу (вернётся на склад) или любое изделие без артикула (металл, вес, описание) → цена → способ выплаты.
• <b>Trade-In</b> — сдаваемое изделие (по артикулу или описанию) и его оценка → новый товар и его цена → бот сам считает доплату.
• <b>Инкассация</b> — изъятие наличных из кассы с комментарием.
• <b>Отчеты</b> — Excel по всем операциям и итог по кассе за сегодня, вчера, неделю, месяц или любой период. Кассир видит свои продажи и операции.
• <b>💻 Касса на ПК</b> — ссылка на кассу в браузере на компьютере магазина.

<b>Склад</b> (менеджер, администратор)
• Добавить товар, пополнить остатки, отчёт об остатках.
• <b>Ценники</b> — PDF для термопринтера с QR-кодом и штрихкодом артикула.
• <b>Импорт каталога</b> — загрузка товаров из Excel/CSV (например, выгрузки Paloma).

<b>Приложения</b>
• /app — касса со сканером QR-кодов ценников (Telegram Mini App).
• <b>Веб-админка</b> (менеджер, администратор) — товары, импорт, ценники, отчёты, персонал в браузере.

<b>Персонал</b> (администратор)
• Добавить пользователя; «Права» — смена роли, телефона, деактивация.

Кнопка <b>Отмена</b> прерывает любую операцию, <b>Назад</b> — возврат в главное меню.
Суммы можно вводить как 15000, 15 000 или 15000,50. Даты — ДД.ММ.ГГГГ."""


def help_text(chat_id):
    """Справка + ссылка на подробную инструкцию для роли пользователя (если задан адрес веб-админки)."""
    from web.manual import ROLE_SLUGS
    slug = ROLE_SLUGS.get((user_data.get(chat_id) or {}).get('role'))
    if not config.WEB_URL:
        return HELP_TEXT
    link = f"{config.WEB_URL}/help/{slug}" if slug else f"{config.WEB_URL}/help"
    return HELP_TEXT + f"\n\n📖 <b>Подробная инструкция:</b> {link}"


def help_command_handler(message: types.Message):
    bot.send_message(message.chat.id, help_text(message.chat.id), parse_mode='HTML',
                     reply_markup=get_keyboard_by_role(message.chat.id))


def register_help_handlers():
    bot.register_message_handler(help_command_handler, commands=['help'])
