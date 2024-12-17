FROM ubuntu:latest

# Обновляем пакеты и устанавливаем Python и pip
RUN apt-get update && \
    apt-get install -y python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Устанавливаем необходимые библиотеки Python
RUN pip3 install --no-cache-dir aiogram telebot

# Создаём рабочую директорию
WORKDIR /app

# Копируем все файлы в образ
COPY . /app

# Команда для запуска вашего приложения
CMD ["python3", "main.py"]