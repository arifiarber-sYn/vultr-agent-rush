FROM python:3.12-slim
WORKDIR /srv
RUN useradd -u 10001 -m runner
COPY app ./app
RUN pip install --no-cache-dir fastapi uvicorn
USER runner
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
