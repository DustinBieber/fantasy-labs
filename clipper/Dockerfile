FROM python:3.12-slim

# ffmpeg with libass for burned-in captions, fontconfig so libass can find fonts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-dejavu-core ca-certificates \
 && (apt-get install -y --no-install-recommends fonts-montserrat || true) \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY clipper ./clipper

# "whisper" adds the local transcription fallback (CPU). Build with --build-arg EXTRAS= for a slimmer image.
ARG EXTRAS=whisper
RUN pip install --no-cache-dir ".${EXTRAS:+[$EXTRAS]}"

ENV CLIPPER_DATA=/data \
    CLIPPER_HOST=0.0.0.0 \
    CLIPPER_PORT=8787 \
    HF_HOME=/data/models \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz')" || exit 1
CMD ["clipper", "serve"]
