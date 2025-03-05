# Структура бота

```
/c:/Users/amuratov/Desktop/Scripts
├── config.py                # Конфигурация бота (TOKEN, параметры БД)
├── database.py              # Работа с базой данных MySQL
├── buttons.py               # Генерация кнопок для интерфейса
├── main.py                  # Точка входа, регистрация обработчиков
└── NewVM
    ├── handlers
    │   ├── start_handler.py    # Авторизация и обработка /start
    │   ├── sales_handler.py    # Логика продаж через state machine
    │   └── report_handler.py   # (Обработчик отчетов, если реализован)
    ├── auth.py              # Логика авторизации пользователя (если используется)
    └── user_management.py   # Управление пользователями
```
