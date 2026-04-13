FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube JS signature solving)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

RUN useradd -m -u 1000 botuser

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/

RUN pip install --no-cache-dir .

RUN mkdir -p /app/logs /home/botuser/.cache/yt-dlp && chown -R botuser:botuser /app /home/botuser/.cache

USER botuser

CMD ["python", "-m", "src.main"]
