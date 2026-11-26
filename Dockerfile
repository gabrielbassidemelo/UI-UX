FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HEALTHMON_HOME=/data HEALTHMON_HOST=0.0.0.0 TZ=America/Sao_Paulo \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 healthmon && mkdir /data && chown healthmon /data && chmod 700 /data
WORKDIR /app
COPY pyproject.toml ./
COPY healthmon ./healthmon
RUN pip install ".[push,otimizacao]" && rm -rf /root/.cache
USER healthmon
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=5m --timeout=10s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=5)"
CMD ["healthmon", "executar"]
