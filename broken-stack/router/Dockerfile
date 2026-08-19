FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi uvicorn httpx
COPY main.py /app/main.py
WORKDIR /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
