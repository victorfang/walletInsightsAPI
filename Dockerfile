FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WALLET_AGE_DB=/app/v2/data/wallet-age-v1.sqlite3 \
    WALLET_INSIGHTS_DB=/app/v2/data/wallet-insights-v1.sqlite3

WORKDIR /app
COPY v2/requirements.txt v2/requirements.txt
RUN python -m pip install --no-cache-dir -r v2/requirements.txt
COPY v2/*.py v2/config.toml v2/docs.html v2/
COPY wallet_age_days/ wallet_age_days/
COPY wallet_insights/ wallet_insights/
COPY eth_wallet_check/ eth_wallet_check/
COPY openapi.json .

# Railway supplies PORT; exec forwards shutdown signals to aiohttp.
CMD ["sh", "-c", "exec python -m v2.app --host 0.0.0.0 --port \"${PORT:-8787}\""]
