#!/usr/bin/env python3
"""Проверка интеграции с Webkassa на тестовой кассе (devkkm) — запросы ровно такие же, как отправляет бот.

Нужен только Python 3.8+ (без установки пакетов). Запуск из папки репозитория:

    WEBKASSA_API_KEY=... WEBKASSA_LOGIN=кассир@почта WEBKASSA_CASHBOX=SWK00000000 python3 tools/webkassa_check.py

Пароль кассира спросит сам (или переменная WEBKASSA_PASSWORD). Параметры:
    --url URL   адрес API (по умолчанию https://devkkm.webkassa.kz — ТОЛЬКО тестовая касса)
    --z         в конце закрыть смену (Z-отчёт)

Что делает: авторизация → продажа из двух позиций (вторая с кодом маркировки) → повтор той же продажи
(как бот после тайм-аута) → возврат первой позиции по основанию → изъятие наличных → запрос с неверным
токеном → X-отчёт → [Z-отчёт]. Все ответы сохраняются в webkassa-check-<дата>.json — пришлите этот файл
разработчику. Токен, пароль и API-ключ в файл не попадают.
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.webkassa import (WebkassaClient, WebkassaError, WebkassaUnavailable, CHECK_PATH,  # noqa: E402
                            MONEY_OPERATION_PATH, OPERATION_SALE, OPERATION_SALE_RETURN, PAYMENT_CASH, ROUND_KOPECKS,
                            MONEY_WITHDRAWAL, position, return_basis)

TEST_MARK = "0104601234567890215TEST" + datetime.now().strftime("%H%M%S") + "\x1d93Ab+/"
SECRET_KEYS = {"Token", "Password", "token", "password"}


class Recorder:
    """Opener для клиента: пропускает запросы и запоминает сырые ответы."""

    def __init__(self):
        self.last = None

    def __call__(self, req, timeout=None):
        self.last = {"path": req.full_url.split("://", 1)[1].split("/", 1)[1], "status": None, "raw": None}
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.last.update(status=e.code, raw=body.decode("utf-8", "replace"))
            raise urllib.error.HTTPError(e.url, e.code, e.msg, e.hdrs, _Body(body))
        body = resp.read()
        self.last.update(status=resp.status, raw=body.decode("utf-8", "replace"))
        return _Resp(body)


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass


class _Resp(_Body):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def masked(body):
    return {k: ("***" if k in SECRET_KEYS else v) for k, v in body.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("WEBKASSA_URL", "https://devkkm.webkassa.kz"))
    ap.add_argument("--z", action="store_true", help="закрыть смену в конце")
    args = ap.parse_args()
    api_key, login = os.environ.get("WEBKASSA_API_KEY"), os.environ.get("WEBKASSA_LOGIN")
    cashbox = os.environ.get("WEBKASSA_CASHBOX")
    if not (api_key and login and cashbox):
        sys.exit("Задайте WEBKASSA_API_KEY, WEBKASSA_LOGIN и WEBKASSA_CASHBOX (ЗНК тестовой кассы).")
    if "devkkm" not in args.url and input(f"{args.url} — это НЕ тестовый стенд. Продолжить? [да/нет] ") != "да":
        sys.exit("Отменено.")
    password = os.environ.get("WEBKASSA_PASSWORD") or getpass.getpass("Пароль кассира Webkassa: ")

    rec = Recorder()
    api = WebkassaClient(args.url, login, password, api_key, opener=rec)
    steps, results = [], {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def step(name, path, body, call):
        print(f"• {name} … ", end="", flush=True)
        entry = {"step": name, "path": path, "request": masked(body) if body else None}
        try:
            data = call()
            entry.update(ok=True, response=data)
            print("ok")
        except WebkassaError as e:
            entry.update(ok=False, error_code=e.code, error_text=e.text)
            print(f"отказ: код {e.code}: {e.text}")
            data = None
        except WebkassaUnavailable as e:
            entry.update(ok=False, unavailable=str(e))
            print(f"недоступно: {e}")
            data = None
        if rec.last:
            raw = rec.last["raw"] or ""
            for secret in (api.token, password, api_key):
                if secret and len(secret) > 3:
                    raw = raw.replace(secret, "***")
            entry.update(http_status=rec.last["status"], raw=raw)
        if name == "Авторизация" and entry.get("ok"):
            entry["response"] = "токен получен (скрыт)"
        steps.append(entry)
        results[name] = data
        time.sleep(1)          # запросы по кассе — последовательно и не вплотную
        return data

    step("Авторизация", "/api/v4/Authorize", {"Login": login, "Password": "***"}, api.authorize)

    sale = {"CashboxUniqueNumber": cashbox, "OperationType": OPERATION_SALE,
            "Positions": [position("Кольцо тест, Au 585, арт. TEST-1", "TEST-1", 1000),
                          position("Серьги тест (маркировка), арт. TEST-2", "TEST-2", 2000, mark=TEST_MARK)],
            "Payments": [{"Sum": 3000, "PaymentType": PAYMENT_CASH}], "Change": 0, "RoundType": ROUND_KOPECKS,
            "ExternalCheckNumber": f"pb-check-{stamp}"}
    sold = step("Продажа: 2 позиции, вторая с кодом маркировки", CHECK_PATH, sale, lambda: api.check(sale))
    if not sold:
        plain = dict(sale, Positions=[sale["Positions"][0], position("Серьги тест, арт. TEST-2", "TEST-2", 2000)],
                     ExternalCheckNumber=f"pb-check-{stamp}-nomark")
        sold = step("Продажа без кода маркировки (раз с кодом не прошла)", CHECK_PATH, plain, lambda: api.check(plain))
        sale = plain
    step("Повтор той же продажи (тот же ExternalCheckNumber)", CHECK_PATH, sale, lambda: api.check(sale))

    if sold:
        ret = {"CashboxUniqueNumber": cashbox, "OperationType": OPERATION_SALE_RETURN,
               "Positions": [sale["Positions"][0]], "Payments": [{"Sum": 1000, "PaymentType": PAYMENT_CASH}],
               "Change": 0, "RoundType": ROUND_KOPECKS, "ExternalCheckNumber": f"pb-return-{stamp}",
               "returnBasisDetails": return_basis(sold, 3000)}
        step("Возврат первой позиции по основанию", CHECK_PATH, ret, lambda: api.check(ret))

    money = {"CashboxUniqueNumber": cashbox, "OperationType": MONEY_WITHDRAWAL, "Sum": 500,
             "ExternalCheckNumber": f"pb-money-{stamp}"}
    done = step("Изъятие 500 ₸ (" + MONEY_OPERATION_PATH + ")", MONEY_OPERATION_PATH, money,
                lambda: api.money_operation(cashbox, MONEY_WITHDRAWAL, 500, money["ExternalCheckNumber"]))
    if not done:
        legacy = "/api/MoneyOperation"
        step("Изъятие 500 ₸ (" + legacy + ")", legacy, money,
             lambda: api.call(legacy, dict(money, ExternalCheckNumber=money["ExternalCheckNumber"] + "-l")))

    good_token = api.token
    api.token = "invalid-token-for-check"
    probe = dict(sale, ExternalCheckNumber=f"pb-token-{stamp}")
    step("Запрос с неверным токеном (бот должен переавторизоваться)", CHECK_PATH, probe,
         lambda: api._post(CHECK_PATH, {"Token": api.token, **probe}))
    api.token = good_token

    step("X-отчёт", "/api/v4/XReport", {"CashboxUniqueNumber": cashbox}, lambda: api.x_report(cashbox))
    if args.z:
        step("Z-отчёт (закрытие смены)", "/api/v4/ZReport", {"CashboxUniqueNumber": cashbox},
             lambda: api.z_report(cashbox))

    out = f"webkassa-check-{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"url": args.url, "cashbox": cashbox, "time": stamp, "steps": steps}, f, ensure_ascii=False,
                  indent=2, default=str)
    ok = sum(1 for s in steps if s.get("ok"))
    print(f"\nГотово: {ok} из {len(steps)} шагов без ошибок. Пришлите файл {out}")


if __name__ == "__main__":
    main()
