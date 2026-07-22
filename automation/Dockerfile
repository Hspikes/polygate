FROM python:3.12-slim

WORKDIR /app

COPY automation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY automation /app/automation

EXPOSE 8020

CMD ["uvicorn", "automation.app.main:app", "--host", "0.0.0.0", "--port", "8020"]
