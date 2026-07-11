FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && . /etc/os-release && curl -fsSL "https://packages.microsoft.com/config/debian/${VERSION_ID}/prod.list" -o /etc/apt/sources.list.d/mssql-release.list \
    && sed -i 's#signed-by=/usr/share/keyrings/microsoft-prod.gpg#signed-by=/usr/share/keyrings/microsoft-prod.gpg#' /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY trading_bot trading_bot
CMD ["python", "-m", "trading_bot.main"]
