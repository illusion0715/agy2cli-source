FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    groupadd --gid 10001 gateway && useradd --uid 10001 --gid gateway --no-create-home gateway && \
    mkdir /data && chown gateway:gateway /data
COPY src ./src
COPY web.py LICENSE ./
RUN chmod -R a+rX /app/src && chmod a+r /app/web.py /app/LICENSE
USER 10001:10001
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM base AS test
COPY --chown=10001:10001 tests ./tests
RUN python -m unittest discover -v

# Default builds produce the production image, never the test fixture image.
FROM base AS runtime
