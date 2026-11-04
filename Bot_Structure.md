# Структура бота

```
main.py                  точка входа: БД, хендлеры, расписание, polling
config.py                настройки из переменных окружения
state.py                 экземпляр бота, сессии и состояния диалогов, отмена операций
auth.py                  вход по номеру, восстановление сессии по chat_id
schema.py                схема БД и идемпотентные миграции при старте
database.py              пул соединений и все запросы
buttons.py               клавиатуры и меню по ролям
user_management.py       Персонал: добавление, роли, телефон, деактивация
handlers/
  common.py              общие шаги (приём изделия от клиента, ввод сумм)
  start_handler.py       /start и авторизация
  help_handler.py        /help
  sales_handler.py       Продажа
  return_handler.py      Возврат
  buyback_handler.py     Скупка
  tradein_handler.py     Trade-In
  cash_withdrawal_handler.py  Инкассация
  report_handler.py      Отчеты по кассе
  stock_handler.py       Склад: добавление, пополнение, остатки
utils/
  common.py              время магазина, разбор сумм/дат/телефонов, форматирование
  report_generator.py    сводка и Excel по всем операциям за период
  scheduled_jobs.py      ежедневный отчёт и уведомление о низком остатке
tests/                   сценарные тесты на реальной БД
```

## Роли

| Роль | Касса | Склад | Персонал |
|---|---|---|---|
| Кассир | ✅ | — | — |
| Менеджер | ✅ | ✅ | — |
| Администратор | ✅ | ✅ | ✅ |

## Состояния операций

Каждая многошаговая операция хранит состояние в `user_state[chat_id]` с префиксом
(`SALES_`, `RETURN_`, `BUYBACK_`, `TRADEIN_`, `WITHDRAWAL_`, `REPORT_`, `STOCK_`, `UM_`),
а данные — в `user_data[chat_id]['op']`. Кнопка «Отмена», `/cancel`, «Назад» и кнопки меню
прерывают любую операцию через `state.cancel_current_operation`.
