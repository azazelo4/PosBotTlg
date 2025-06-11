from database import get_user_by_phone # get_user_by_phone уже нормализует внутри
import logging
logger = logging.getLogger(__name__)
def authorize_user(phone_number_raw):
    # phone_number_raw передается в get_user_by_phone, который сам нормализует его.
    try:
        user_data = get_user_by_phone(phone_number_raw) # Эта функция уже должна нормализовать
        if user_data:
            logger.debug(f"Авторизация пользователя (данные из БД): {user_data} по номеру (raw): {phone_number_raw}")
            if user_data['is_active'] == 0:
                logger.warning(f"Пользователь с телефоном {phone_number_raw} (нормализован как {user_data.get('phone_number')}) деактивирован.")
                return None, "Ваш аккаунт деактивирован. Пожалуйста, обратитесь к администратору."
            return user_data, None
        else:
            logger.warning(f"Пользователь с телефоном {phone_number_raw} не найден (после нормализации).")
            return None, "Пользователь не найден."
    except Exception as e:
        logger.exception(f"Ошибка при авторизации пользователя с номером {phone_number_raw}.")
        return None, "Произошла ошибка при авторизации. Попробуйте еще раз."
