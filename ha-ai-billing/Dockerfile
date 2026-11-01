# One image, two modes (CMD is the same):
#  * Home Assistant add-on: /data/options.json exists -> run.sh converts it
#  * Standalone docker compose: no /data -> plain `python -m app.main`
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY addon ./addon
COPY run.sh .
RUN chmod +x run.sh

CMD ["/app/run.sh"]
