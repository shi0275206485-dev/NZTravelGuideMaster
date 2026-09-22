# TravelGuideMaster API.
#
# Only the application package is copied in. backend/.env and backend/data
# never enter the image: the secrets arrive as environment variables from
# the server's deploy/.env, and the POI cache is mounted as a volume so it
# can be replaced without a rebuild and survives one.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Unprivileged, with uid 1000 so that a data/ directory created by the
# default login user on the host (ec2-user on Amazon Linux, ubuntu on
# Ubuntu — both 1000) is writable from inside without a chown. SQLite needs
# to write there: the usage limits, weather and routes are all cached in
# the same file as the POIs.
RUN useradd --uid 1000 --create-home app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# --proxy-headers: take the client address from Caddy's X-Forwarded-For,
#   which the per-address limits depend on. Trusting every forwarder ("*")
#   is safe only because this port is never published — compose puts the
#   API on an internal network that nothing but Caddy can reach.
# One worker: endpoints are synchronous and run in a thread pool, which is
#   enough for a demo, and the usage counters live in SQLite regardless.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
