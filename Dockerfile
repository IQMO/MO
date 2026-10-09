# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ARG MO_UID=10001
ARG MO_GID=10001

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MO_STATE_HOME=/home/user/.mo \
    MO_CONFIG=/home/user/.mo/config.yaml \
    MO_PROJECT_CWD=/workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git openssh-client ripgrep tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${MO_GID}" user \
    && useradd --uid "${MO_UID}" --gid "${MO_GID}" --create-home --shell /bin/sh user \
    && mkdir -p /app /workspace /home/user/.mo \
    && chown -R user:user /workspace /home/user

WORKDIR /app

COPY requirements.txt requirements-everywhere.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip setuptools \
    && python -m pip install --no-cache-dir -r requirements.txt -r requirements-everywhere.txt

# Copy only runtime/product sources. Maintainer overlays, Git state, Android
# build inputs, credentials, local config, and private profile data never enter
# the image context or the resulting image.
COPY core ./core
COPY interface ./interface
COPY tools ./tools
COPY mo_desktop ./mo_desktop
COPY mo_everywhere ./mo_everywhere
COPY mo.py mo_service.py config.example.yaml pyproject.toml ./

RUN chmod -R a=rX /app

USER user
WORKDIR /workspace

VOLUME ["/home/user/.mo"]

ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/mo_service.py"]
CMD ["--surface", "server"]
