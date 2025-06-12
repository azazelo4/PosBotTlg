from database import get_user_by_phone # get_user_by_phone already normalizes internally
import logging

logger = logging.getLogger(__name__)

def authorize_user(phone_number_raw: str) -> tuple[dict | None, str | None]:
    """
    Authorizes a user based on the raw phone number.
    Returns a tuple: (user_data_dict, error_message_str).
    If authorization is successful, user_data_dict is populated and error_message_str is None.
    If authorization fails, user_data_dict is None and error_message_str contains the reason.
    """
    try:
        user_data = get_user_by_phone(phone_number_raw) # This function already normalizes

        if user_data:
            logger.debug(f"Authorization attempt for raw phone '{phone_number_raw}'. User data from DB: {user_data}")
            if user_data.get('is_active') == 0: # Ensure 'is_active' is checked correctly
                logger.warning(f"User with raw phone '{phone_number_raw}' (normalized: {user_data.get('phone_number')}) is deactivated.")
                return None, "Ваш аккаунт деактивирован. Пожалуйста, обратитесь к администратору."
            # User found and is active
            return user_data, None
        else:
            # User not found by get_user_by_phone (which means after normalization)
            logger.warning(f"User with raw phone '{phone_number_raw}' not found (after normalization attempt).")
            return None, "Пользователь не найден."
    except Exception as e:
        logger.exception(f"Exception during user authorization for raw phone '{phone_number_raw}'.")
        return None, "Произошла ошибка при авторизации. Пожалуйста, попробуйте еще раз."