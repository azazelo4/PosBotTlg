"""Клиент API Webkassa (интеграторы, протокол v4): авторизация, чек, внесение/изъятие, X- и Z-отчёт.

По документации интегратора 2.0.4 (Postman-коллекция Webkassa):
- все запросы — POST с JSON, ключ интегратора в заголовке `X-API-KEY`;
- `api/v4/Authorize` {Login, Password} → Data.Token, токен передаётся в теле следующих запросов;
- ответ: {"Data": {...}} при успехе или {"Errors": [{"Code": N, "Text": "..."}]};
- запросы по одной кассе — строго последовательно (это обеспечивает вызывающий код, см. fiscal.cashbox_lock).

Не описано в доступной документации и требует проверки на тестовой кассе devkkm:
- код ошибки «срок действия токена истёк» (здесь — TOKEN_EXPIRED_CODES, при нём одна переавторизация);
- путь и поля внесения/изъятия (MONEY_OPERATION_PATH).
"""
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

AUTHORIZE_PATH = "/api/v4/Authorize"
CHECK_PATH = "/api/v4/check"
MONEY_OPERATION_PATH = "/api/v4/MoneyOperation"
X_REPORT_PATH = "/api/v4/XReport"
Z_REPORT_PATH = "/api/v4/ZReport"

TOKEN_EXPIRED_CODES = {2}
TIMEOUT = 30

# Коды из документации
OPERATION_SALE, OPERATION_SALE_RETURN = 2, 3
PAYMENT_CASH, PAYMENT_CARD, PAYMENT_MOBILE = 0, 1, 4
TAX_NONE, TAX_VAT = 0, 100
POSITION_GOODS = 0
UNIT_PIECE = 796
ROUND_KOPECKS = 0            # каждая позиция и итог — до 2 знаков, без округления до тенге
MONEY_DEPOSIT, MONEY_WITHDRAWAL = 0, 1


class WebkassaError(Exception):
    """Ошибка, которую вернул Webkassa (повтор того же запроса не поможет)."""

    def __init__(self, code, text):
        super().__init__(f"Webkassa, код {code}: {text}")
        self.code = code
        self.text = text


class WebkassaUnavailable(Exception):
    """Нет связи, тайм-аут или ошибка сервера Webkassa — запрос можно повторить позже."""


class WebkassaClient:
    def __init__(self, base_url, login, password, api_key, opener=None, timeout=TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.login, self.password, self.api_key = login, password, api_key
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self.token = None

    # ------------------------------------------------------------ транспорт
    def _post(self, path, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
            "X-API-KEY": self.api_key or ""})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raw = e.read() if hasattr(e, "read") else b""
            if e.code >= 500 or e.code in (408, 429):
                raise WebkassaUnavailable(f"HTTP {e.code} от Webkassa") from e
            if not raw:
                raise WebkassaError(e.code, f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WebkassaUnavailable(f"нет связи с Webkassa: {getattr(e, 'reason', e)}") from e
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError as e:
            raise WebkassaUnavailable("Webkassa вернул не JSON") from e
        errors = payload.get("Errors") or payload.get("errors")
        if errors:
            first = errors[0]
            raise WebkassaError(first.get("Code", first.get("code")), first.get("Text", first.get("text", "")))
        return payload.get("Data", payload.get("data"))

    def authorize(self):
        data = self._post(AUTHORIZE_PATH, {"Login": self.login, "Password": self.password})
        self.token = (data or {}).get("Token")
        if not self.token:
            raise WebkassaError(0, "в ответе на авторизацию нет токена")
        return self.token

    def call(self, path, body):
        """Запрос с токеном. При истёкшем токене — одна переавторизация и повтор."""
        if not self.token:
            self.authorize()
        try:
            return self._post(path, {"Token": self.token, **body})
        except WebkassaError as e:
            if e.code not in TOKEN_EXPIRED_CODES:
                raise
            logger.info("Webkassa: токен истёк, авторизуюсь заново")
            self.authorize()
            return self._post(path, {"Token": self.token, **body})

    # ------------------------------------------------------------ операции
    def check(self, body):
        return self.call(CHECK_PATH, body)

    def money_operation(self, cashbox, operation_type, amount, external_number):
        return self.call(MONEY_OPERATION_PATH, {"CashboxUniqueNumber": cashbox, "OperationType": operation_type,
                                                "Sum": float(amount), "ExternalCheckNumber": external_number})

    def x_report(self, cashbox):
        return self.call(X_REPORT_PATH, {"CashboxUniqueNumber": cashbox})

    def z_report(self, cashbox):
        return self.call(Z_REPORT_PATH, {"CashboxUniqueNumber": cashbox})
