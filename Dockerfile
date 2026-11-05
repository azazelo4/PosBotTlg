FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Шрифт с кириллицей для ценников (PDF)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем, чтобы кешировались между сборками
COPY requirements.txt .
RUN pip install -r requirements.txt

# Код приложения
COPY . /app

CMD ["python", "main.py"]
