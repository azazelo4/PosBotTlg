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

# Код приложения. deploy/ тоже внутри: установщик и `posbot update` берут оттуда compose-файл и скрипты
COPY . /app

# Версия (тег релиза или sha коммита) — проставляет CI при публикации образа
ARG POSBOT_VERSION=dev
ENV POSBOT_VERSION=${POSBOT_VERSION}
LABEL org.opencontainers.image.source="https://github.com/azazelo4/PosBotTlg" \
      org.opencontainers.image.title="PosBotTlg" \
      org.opencontainers.image.version="${POSBOT_VERSION}"

CMD ["python", "main.py"]
