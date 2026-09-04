# syntax=docker/dockerfile:1
FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE: no .pyc litter in the image layer.
# PYTHONUNBUFFERED: logs reach `docker logs` immediately rather than sitting
# in a block buffer, which otherwise makes a crashing container look silent.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before application code: this layer is keyed only on
# requirements.txt, so editing a source file rebuilds from the COPY below
# rather than reinstalling every package.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root at runtime. Created after the install so the pip layer isn't
# invalidated by user setup, and owns /app so nothing needs to write as root.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Hits the existing liveness route (app/main.py::health_check). Uses urllib
# rather than curl/wget - neither is present in python:3.11-slim, and adding
# one just for a healthcheck would grow the image for nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
