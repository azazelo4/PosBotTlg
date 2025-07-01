FROM python:3.10-slim-bullseye

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y python3-venv && rm -rf /var/lib/apt/lists/*

# Создаём виртуальное окружение
WORKDIR /app
RUN python3 -m venv venv

# Активируем виртуальное окружение и устанавливаем зависимости из requirements.txt
COPY requirements.txt .
RUN ./venv/bin/pip install -r requirements.txt

# Копируем код приложения
COPY . /app
# Указываем точку входа
CMD ["./venv/bin/python", "main.py"]
