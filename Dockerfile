FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV DENO_INSTALL=/usr/local
ENV PATH="/usr/local/bin:${PATH}"

ARG DENO_VERSION=2.9.5
ARG BGUTIL_PROVIDER_VERSION=1.3.1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deno.land/install.sh | sh -s "v${DENO_VERSION}"

# Build the persistent BgUtils HTTP provider into the image. It binds only to
# the container loopback interface at runtime; port 4416 is never exposed.
RUN mkdir -p /opt/bgutil \
    && curl -fsSL "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/${BGUTIL_PROVIDER_VERSION}.tar.gz" \
       | tar -xz --strip-components=1 -C /opt/bgutil \
    && sed -i \
       -e 's/host: "::"/host: "127.0.0.1"/g' \
       -e 's/host: "0.0.0.0"/host: "127.0.0.1"/g' \
       -e 's/\[::\]/127.0.0.1/g' \
       -e 's/0\.0\.0\.0/127.0.0.1/g' \
       /opt/bgutil/server/src/main.ts \
    && test "$(grep -c 'host: "127.0.0.1"' /opt/bgutil/server/src/main.ts)" -ge 2 \
    && ! grep -Eq 'host: "(::|0\.0\.0\.0)"' /opt/bgutil/server/src/main.ts \
    && cd /opt/bgutil/server \
    && deno install --allow-scripts=npm:canvas --frozen

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY start_services.py ./start_services.py

RUN mkdir -p storage/temp /app/secrets

EXPOSE 8000

CMD ["python", "start_services.py"]
