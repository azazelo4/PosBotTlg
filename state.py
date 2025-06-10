# state.py
from telebot import TeleBot
from config import TOKEN # Assuming config.py is at the root
import logging

logger = logging.getLogger(__name__)

bot = TeleBot(TOKEN) 
user_data = {}    # chat_id -> {'role': ..., 'id': DB_USER_ID, 'store_id': DB_STORE_ID, 'name': ..., 'is_active': ..., 'operation_data': {}}
user_state = {}   # chat_id -> "CURRENT_STATE_FOR_FSM"