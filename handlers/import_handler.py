"""Импорт каталога из Excel/CSV через бота: магазин по умолчанию → файл → предпросмотр → загрузка."""
import logging

from buttons import create_cancel_markup, create_choice_markup, create_stock_management_buttons
from handlers.common import store_names, store_id_by_name, STOCK_ROLES, in_state, is_button, register_simple_cancel
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.catalog_import import build_plan, apply_plan
from utils.common import h

logger = logging.getLogger(__name__)

STORE, FILE, CONFIRM = "IMPORT_STORE", "IMPORT_FILE", "IMPORT_CONFIRM"
FROM_FILE = "Магазин указан в файле"
APPLY_ALL, APPLY_NEW = "Загрузить: новые и обновить существующие", "Загрузить только новые"
MAX_FILE = 10 * 1024 * 1024


def stock_menu(chat_id, text, **kw):
    bot.send_message(chat_id, text, reply_markup=create_stock_management_buttons(), **kw)


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, STORE)
    bot.send_message(chat_id, "Импорт каталога из Excel (.xlsx) или CSV.\n"
                              "В какой магазин загружать товары, если в файле нет колонки «Магазин»?",
                     reply_markup=create_choice_markup(store_names() + [FROM_FILE], 2))


def on_store(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    store_id = store_id_by_name(text)
    if not store_id and text != FROM_FILE:
        bot.send_message(chat_id, "Выберите вариант кнопкой.", reply_markup=create_choice_markup(store_names() + [FROM_FILE], 2))
        return
    get_op(chat_id)['store'] = store_id
    user_state[chat_id] = FILE
    bot.send_message(chat_id, "Пришлите файл документом. Нужны колонки как минимум «Артикул» и «Цена»; "
                              "также распознаются «Наименование», «Тип», «Металл», «Проба», «Вес», «Размер», "
                              "«Вставки», «Остаток», «Мин. цена», «Магазин».", reply_markup=create_cancel_markup())


def on_file(message):
    chat_id = message.chat.id
    doc = message.document
    if doc is None:
        bot.send_message(chat_id, "Пришлите файл документом (скрепка → Файл).", reply_markup=create_cancel_markup())
        return
    if doc.file_size and doc.file_size > MAX_FILE:
        bot.send_message(chat_id, "Файл больше 10 МБ. Разбейте его на части.", reply_markup=create_cancel_markup())
        return
    bot.send_chat_action(chat_id, 'typing')
    try:
        data = bot.download_file(bot.get_file(doc.file_id).file_path)
        plan = build_plan(data, doc.file_name or '', default_store=get_op(chat_id).get('store'))
    except ValueError as e:
        bot.send_message(chat_id, f"❌ {e}", reply_markup=create_cancel_markup())
        return
    except Exception:
        logger.exception("Chat %s: ошибка разбора файла импорта", chat_id)
        bot.send_message(chat_id, "❌ Не удалось прочитать файл. Проверьте, что это .xlsx или .csv.", reply_markup=create_cancel_markup())
        return
    if not plan.rows:
        bot.send_message(chat_id, h(plan.summary()) + "\n\nЗагружать нечего. Исправьте файл и пришлите снова.",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['plan'] = plan
    user_state[chat_id] = CONFIRM
    options = [APPLY_ALL, APPLY_NEW] if plan.existing else [APPLY_NEW]
    bot.send_message(chat_id, "<b>Предпросмотр импорта</b>\n" + h(plan.summary()), parse_mode='HTML',
                     reply_markup=create_choice_markup(options, 1))


def on_file_text(message):
    bot.send_message(message.chat.id, "Жду файл .xlsx или .csv документом. «Отмена» — прервать.", reply_markup=create_cancel_markup())


def on_confirm(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in (APPLY_ALL, APPLY_NEW):
        bot.send_message(chat_id, "Выберите вариант кнопкой или «Отмена».")
        return
    plan = get_op(chat_id)['plan']
    ok, result = apply_plan(plan, update_existing=text == APPLY_ALL)
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: импорт каталога %s", chat_id, result)
        stock_menu(chat_id, f"✅ Импорт завершён: добавлено {result['created']}, обновлено {result['updated']}"
                            + (f", пропущено существующих {result['skipped']}" if result['skipped'] else "") + ".")
    else:
        stock_menu(chat_id, f"❌ Импорт не выполнен, ничего не изменено: {result}")


def register_import_handlers():
    register_simple_cancel("IMPORT_", "Загрузка каталога", menu=stock_menu)
    bot.register_message_handler(start, func=is_button("Импорт каталога", *STOCK_ROLES))
    bot.register_message_handler(on_store, func=in_state(STORE))
    bot.register_message_handler(on_file, func=in_state(FILE), content_types=['document'])
    bot.register_message_handler(on_file_text, func=in_state(FILE), content_types=['text', 'photo'])
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
