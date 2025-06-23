FROM python:3.10

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y python3-venv && rm -rf /var/lib/apt/lists/*

# Создаём виртуальное окружение
WORKDIR /app
RUN python3 -m venv venv

# Активируем виртуальное окружение и устанавливаем зависимости
RUN ./venv/bin/pip install aiogram telebot pymysql mysql-connector-python openpyxl APScheduler

# Копируем код приложения
COPY . /app

# Указываем точку входа
CMD ["./venv/bin/python", "main.py"]
