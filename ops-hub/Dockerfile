FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATABASE_URL=sqlite:////data/opshub.db MUSE_LOCAL_DIR=/data/handoff
COPY pyproject.toml README.md ./
COPY opshub ./opshub
RUN pip install --no-cache-dir .
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "opshub", "serve", "--host", "0.0.0.0", "--port", "8000"]
