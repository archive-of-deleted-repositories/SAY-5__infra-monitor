# --- builder ---------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel \
    && /opt/venv/bin/pip install .

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INFRA_MONITOR_DATABASE_URL=sqlite:////data/infra_monitor.db

RUN useradd --create-home --uid 10001 monitor \
    && mkdir -p /data \
    && chown monitor:monitor /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=monitor:monitor scripts /opt/infra-monitor/scripts
COPY --chown=monitor:monitor config /opt/infra-monitor/config

USER monitor
WORKDIR /opt/infra-monitor
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/healthz').read(); sys.exit(0)" || exit 1

ENTRYPOINT ["infra-monitor"]
CMD ["dashboard", "--host", "0.0.0.0", "--port", "8080"]
