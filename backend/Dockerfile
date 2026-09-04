# The API, for Cloud Run.
#
# Two things about this file are worth reading rather than skimming.
#
# The embedding model is baked into the image at build time. Downloading
# 130 MB on the first request would make that request time out, and
# Cloud Run scales to zero, so "the first request" happens again every
# time the service has been idle. Paying for it once at build time turns
# a recurring 30 second stall into nothing.
#
# The image installs requirements-serve.txt, not requirements.txt. See
# that file for what is left out and why it is safe.

FROM python:3.12-slim

# Keep the image small and the logs honest.
#   PYTHONUNBUFFERED   print() reaches Cloud Run logs immediately rather
#                      than sitting in a buffer until the process exits
#   PYTHONDONTWRITEBYTECODE  no .pyc files to ship
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is what psycopg2-binary talks to. The binary wheel bundles most
# of what it needs, but not this.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, in their own layer. Code changes far more often
# than the dependency list, so this layer stays cached across rebuilds
# and a redeploy takes seconds instead of minutes.
COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

# Download the embedding weights now, not on the first request.
ENV MODEL_DIR=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/app/models')"

# The application. mcp_server is deliberately not copied: it is a
# separate stdio entry point and the HTTP API never imports it.
COPY app ./app

# The prebuilt table vectors and the schema snapshot the drift check
# compares against. Both are needed at runtime and neither can be
# rebuilt here, because building them needs a database connection.
COPY data ./data

# Cloud Run sends traffic to whatever is in $PORT and does not promise
# it will be 8080, so it is read at runtime rather than hardcoded.
# One worker: this service spends its time waiting on the model provider,
# not on CPU, and Cloud Run runs more containers when it needs more
# concurrency.
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
