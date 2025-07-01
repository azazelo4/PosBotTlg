import logging
from telebot import types
from state import bot, user_data, user_state
from database import create_cash_withdrawal_record
from buttons import create_cancel_markup, create_confirmation_markup, create_cash_operations_buttons
from handlers.sales_handler import parse_price # Используем парсер из sales_handler

logger = logging.getLogger(__name__)

# States for cash withdrawal
AWAITING_WITHDRAWAL_AMOUNT = "AWAITING_WITHDRAWAL_AMOUNT"
AWAITING_WITHDRAWAL_COMMENT = "AWAITING_WITHDRAWAL_COMMENT"
AWAITING_WITHDRAWAL_CONFIRMATION = "AWAITING_WITHDRAWAL_CONFIRMATION"

withdrawal_handler_instances = {}

class CashWithdrawal:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.withdrawal_data = {}

    def start_withdrawal(self):
        logger.info(f"Chat {self.chat_id}: Starting cash withdrawal process.")
        user_state[self.chat_id] = AWAITING_WITHDRAWAL_AMOUNT
        bot.send_message(self.chat_id, "Введите сумму изъятия (инкассации):", reply_markup=create_cancel_markup())

    def handle_amount(self, message: types.Message):
        amount = parse_price(message.text)
        if amount is None or amount <= 0:
            bot.send_message(self.chat_id, "Некорректная сумма. Введите положительное число или нажмите 'Отмена'.", reply_markup=create_cancel_markup())
            return

        self.withdrawal_data['amount'] = amount
        user_state[self.chat_id] = AWAITING_WITHDRAWAL_COMMENT
        logger.debug(f"Chat {self.chat_id}: Received withdrawal amount: {amount}")
        bot.send_message(self.chat_id, "Введите комментарий (например, 'в банк', 'на расходы') или введите '-', если комментария нет:", reply_markup=create_cancel_markup())

    def handle_comment(self, message: types.Message):
        comment = message.text.strip()
        self.withdrawal_data['comment'] = comment if comment != '-' else None
        logger.debug(f"Chat {self.chat_id}: Received withdrawal comment: {comment}")
        self.proceed_to_confirmation()

    def proceed_to_confirmation(self):
        amount = self.withdrawal_data['amount']
        comment = self.withdrawal_data.get('comment', 'Нет')

        user_state[self.chat_id] = AWAITING_WITHDRAWAL_CONFIRMATION
        confirmation_text = (
            f"Подтвердите инкассацию:\n\n"
            f"Сумма: {amount:.2f}\n"
            f"Комментарий: {comment}\n\n"
            f"Подтвердить операцию?"
        )
        bot.send_message(self.chat_id, confirmation_text, reply_markup=create_confirmation_markup())

    def handle_confirmation(self, message: types.Message):
        choice = message.text.strip()
        if choice == "Подтвердить":
            logger.info(f"Chat {self.chat_id}: Cash withdrawal confirmed.")
            
            db_user_id = user_data[self.chat_id].get('id')
            store_id = user_data[self.chat_id].get('store_id')

            if not db_user_id or not store_id:
                logger.error(f"Chat {self.chat_id}: User ID or Store ID not found for withdrawal. User data: {user_data[self.chat_id]}")
                bot.send_message(self.chat_id, "Ошибка: Не удалось определить пользователя или магазин. Операция отменена.", reply_markup=create_cash_operations_buttons())
                self.clean_up_state()
                return

            success, db_msg = create_cash_withdrawal_record(
                amount=self.withdrawal_data['amount'],
                comment=self.withdrawal_data.get('comment'),
                user_id=db_user_id,
                store_id=store_id
            )
            bot.send_message(self.chat_id, db_msg, reply_markup=create_cash_operations_buttons())
        
        elif choice == "Отмена":
            logger.info(f"Chat {self.chat_id}: Withdrawal cancelled at confirmation.")
            bot.send_message(self.chat_id, "Инкассация отменена.", reply_markup=create_cash_operations_buttons())
        
        else:
            bot.send_message(self.chat_id, "Пожалуйста, используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())
            return

        self.clean_up_state()

    def cancel_withdrawal(self):
        logger.info(f"Chat {self.chat_id}: Withdrawal operation cancelled by user.")
        bot.send_message(self.chat_id, "Инкассация отменена.", reply_markup=create_cash_operations_buttons())
        self.clean_up_state()

    def clean_up_state(self):
        user_state.pop(self.chat_id, None)
        withdrawal_handler_instances.pop(self.chat_id, None)
        logger.debug(f"Chat {self.chat_id}: Cleaned up withdrawal state.")

def register_withdrawal_handlers():
    @bot.message_handler(func=lambda message: message.text == "Инкассация" and user_data.get(message.chat.id, {}).get('role') in ['admin', 'cashier', 'manager'])
    def start_withdrawal_handler(message: types.Message):
        chat_id = message.chat.id
        withdrawal_handler_instances[chat_id] = CashWithdrawal(chat_id)
        withdrawal_handler_instances[chat_id].start_withdrawal()

    @bot.message_handler(func=lambda message: user_state.get(message.chat.id) == AWAITING_WITHDRAWAL_AMOUNT)
    def handle_amount_message(message: types.Message):
        if message.text == "Отмена": withdrawal_handler_instances[message.chat.id].cancel_withdrawal(); return
        withdrawal_handler_instances[message.chat.id].handle_amount(message)

    @bot.message_handler(func=lambda message: user_state.get(message.chat.id) == AWAITING_WITHDRAWAL_COMMENT)
    def handle_comment_message(message: types.Message):
        if message.text == "Отмена": withdrawal_handler_instances[message.chat.id].cancel_withdrawal(); return
        withdrawal_handler_instances[message.chat.id].handle_comment(message)

    @bot.message_handler(func=lambda message: user_state.get(message.chat.id) == AWAITING_WITHDRAWAL_CONFIRMATION)
    def handle_confirmation_message(message: types.Message):
        # Cancel is handled inside the method
        withdrawal_handler_instances[message.chat.id].handle_confirmation(message)