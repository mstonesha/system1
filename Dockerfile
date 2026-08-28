FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends postgresql-client \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir -r requirements-dev.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY ./app ./app
COPY ./migrations ./migrations
COPY ./alembic.ini ./alembic.ini

# Production-safe default. Local Compose overrides with --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
