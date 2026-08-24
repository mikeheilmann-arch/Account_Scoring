FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY certifid_account_scoring ./certifid_account_scoring

ENTRYPOINT ["python", "-m", "certifid_account_scoring.batch_job"]
