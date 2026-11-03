FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as the host owner's uid so files in the mounted data folder stay theirs.
ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" app && useradd -u "$UID" -g "$GID" -M app
COPY finance/ finance/
USER app

EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4)"
CMD ["uvicorn", "finance.web:app", "--host", "0.0.0.0", "--port", "8787", "--no-access-log"]
