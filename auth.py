from database import get_user_by_phone
import logging
logger = logging.getLogger(__name__)

def authorize_user(phone_number):
    user_data = get_user_by_phone(phone_number)
    logger.debug(f"Авторизация пользователя: {user_data}")
    if user_data:
        if user_data['is_active'] == 0:
            logger.warning(f"Пользователь с телефоном {phone_number} деактивирован.")
            return None, "Ваш аккаунт деактивирован. Пожалуйста, обратитесь к администратору."
        return user_data, None
    else:
        logger.warning(f"Пользователь с телефоном {phone_number} не найден.")
        return None, "Пользователь не найден."

