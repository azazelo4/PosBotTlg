from telebot import TeleBot
from config import TOKEN

bot = TeleBot(TOKEN)
user_data = {}
user_state = {}  # New dictionary to track user states