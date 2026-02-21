# fuuka-cata-link

Telegram bot that automatically extracts and reposts media from social media links shared in group chats. Drop a link, get the media — no need to leave Telegram.

## Supported Platforms

| Platform | Content Types |
|---|---|
| Twitter / X | Tweets with images and videos |
| YouTube Shorts | Short-form videos |
| Instagram | Posts, reels, carousels |
| TikTok | Videos |
| Facebook | Posts, reels |
| GitHub | Commits, pull requests |
| Reddit | Images, videos, text posts |

## How It Works

The bot watches for social media links in group messages. When it detects one, it extracts the media using a three-tier fallback strategy:

1. **Platform-specific API** — fastest (e.g. vxtwitter API for Twitter)
2. **yt-dlp** — universal fallback for most video platforms
3. **Headless browser** — last resort via Playwright

Extracted media is sent back as a reply with a caption containing the author and a link to the original post. If a link is sent as a spoiler, the media is also sent with spoiler protection.

## Setup

### Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- ffmpeg (for video processing)

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Required:
- `TELEGRAM_BOT_TOKEN` — your bot token

Optional:
- `ALLOWED_CHATS` — comma-separated chat IDs to restrict the bot to specific groups
- `TWITTER_BEARER_TOKEN` — for Twitter API access
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — for Reddit API access
- `COOKIES_FILE` — path to a cookies.txt file for authenticated scraping (Instagram, Facebook)
- `MAX_FILE_SIZE_MB` — max file size to download (default: 50)
- `DOWNLOAD_TIMEOUT_SECONDS` — download timeout (default: 30)
- `CONCURRENT_DOWNLOADS` — max parallel downloads (default: 3)
- `LOG_LEVEL` — logging level (default: INFO)

### Local Installation

```bash
pip install -e ".[dev]"

# Optional: browser fallback support
pip install -e ".[browser]"
playwright install
```

Run the bot:

```bash
python -m src.main
```

## Docker Deployment

### Quick Start

```bash
# 1. Clone and configure
git clone <repo-url> && cd fuuka-cata-link
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN

# 2. Build and run
docker compose up -d
```

### docker-compose.yml

The included `docker-compose.yml` runs the bot with automatic restarts and persistent log storage:

```yaml
services:
  telegram-bot:
    build: .
    container_name: fuuka-cata-link-bot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - bot-logs:/app/logs
      # Uncomment for authenticated scraping (Instagram, Facebook):
      # - ./cookies.txt:/app/cookies.txt:ro
```

### Common Operations

```bash
# View logs
docker compose logs -f

# Rebuild after code changes
docker compose up -d --build

# Stop the bot
docker compose down
```

### Authenticated Scraping

Some platforms (Instagram, Facebook) require cookies for reliable extraction. To enable this:

1. Export cookies from your browser using a browser extension (e.g. "Get cookies.txt LOCALLY")
2. Save the file as `cookies.txt` in the project root
3. Uncomment the cookies volume mount in `docker-compose.yml`
4. Set `COOKIES_FILE=/app/cookies.txt` in your `.env`

## Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=src --cov-report=html

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

### Adding a New Platform

1. Create `src/scrapers/<platform>.py` extending `BaseScraper`
2. Implement `_primary_extract(url)` returning `ScrapedMedia`
3. Register the class in `src/scrapers/__init__.py`
4. Add the URL pattern to `src/utils/link_detector.py`

## Project Structure

```
src/
  bot/
    handlers.py       # Message routing, media sending
    filters.py        # Link detection filter, chat whitelist
    middlewares.py     # Logging, rate limiting (5 req/60s per user)
  scrapers/
    base.py           # BaseScraper ABC, ScrapedMedia/MediaItem dataclasses
    twitter.py        # Twitter/X scraper
    youtube.py        # YouTube Shorts scraper
    instagram.py      # Instagram scraper
    tiktok.py         # TikTok scraper
    facebook.py       # Facebook scraper
    github.py         # GitHub commits/PRs scraper
    reddit.py         # Reddit scraper
  utils/
    link_detector.py  # URL pattern matching, platform detection
    media_handler.py  # Concurrent downloads, image optimization
    formatters.py     # Caption/text formatting
    ytdlp.py          # yt-dlp wrapper
  config.py           # pydantic-settings config
  main.py             # Entry point
```
