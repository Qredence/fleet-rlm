FROM docker.io/library/python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /uvx /usr/local/bin/
COPY backend/snapshot/requirements.lock /tmp/requirements.lock
RUN uv pip sync --system --no-cache --only-binary :all: --require-hashes /tmp/requirements.lock \
    && rm /tmp/requirements.lock

COPY backend/skills/ /opt/skills/
RUN chmod -R a-w /opt/skills \
    && mkdir -p /workspace/files /workspace/artifacts
WORKDIR /workspace
