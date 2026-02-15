# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot that automatically extracts and reposts media from social media links shared in group chats. Built with Python 3.11+, aiogram (async Telegram framework), and yt-dlp.

## Commands

```bash
# Install
pip install -e ".[dev]"          # Core + dev dependencies
pip install -e ".[browser]"      # Optional browser support
playwright install               # Browser binaries (if using browser fallback)

# Run
python -m src.main               # Start the bot (requires .env with TELEGRAM_BOT_TOKEN)

# Test
pytest                           # All tests
pytest --cov=src --cov-report=html  # With coverage
pytest tests/test_scrapers/test_twitter.py  # Single test file
# Note: asyncio_mode = "auto" — async tests are auto-detected

# Lint & Format
ruff check src/ tests/           # Lint
ruff check --fix src/ tests/     # Auto-fix
ruff format src/ tests/          # Format
```

Ruff config: Python 3.11 target, line length 100, rules E/F/I/W.

## Architecture

### Three-Tier Fallback Extraction (Strategy Pattern)

Every scraper extends `BaseScraper` which implements a fallback chain automatically:
1. **Primary**: Platform-specific API or fastest method (e.g., vxtwitter API for Twitter)
2. **Secondary**: yt-dlp universal downloader
3. **Tertiary**: Headless browser extraction (playwright)

Subclasses implement `_primary_extract()` at minimum. The base class handles fallback orchestration.

### Message Flow

```
Message → LoggingMiddleware → RateLimitMiddleware
  → AllowedChat filter → ContainsSupportedLink filter (injects detected_links)
  → handle_media_link() → lookup scraper from _SCRAPER_MAP → scraper.extract(url)
  → _send_result() (text / photo / video / media group max 10)
```

### Key Modules

- **`src/scrapers/base.py`** — `BaseScraper` ABC, `ScrapedMedia`/`MediaItem` dataclasses, `MediaType` enum
- **`src/scrapers/__init__.py`** — `SCRAPERS` list for registration; lazy-loaded into `_SCRAPER_MAP` at startup
- **`src/bot/handlers.py`** — Message routing, scraper orchestration, two-phase media sending (pre-downloaded vs URL-based)
- **`src/bot/filters.py`** — `ContainsSupportedLink` (regex link detection), `AllowedChat` (whitelist)
- **`src/bot/middlewares.py`** — `LoggingMiddleware`, `RateLimitMiddleware` (token bucket, 5 req/60s per user)
- **`src/utils/link_detector.py`** — URL pattern matching, platform detection (`Platform` enum), URL cleaning
- **`src/utils/media_handler.py`** — Concurrent downloads with semaphore, image optimization via Pillow
- **`src/utils/ytdlp.py`** — yt-dlp wrapper: downloads to temp dir, returns bytes, handles signed/temporary URLs
- **`src/config.py`** — `pydantic-settings` based config, auto-loads `.env`

### Adding a New Platform Scraper

1. Create `src/scrapers/newplatform.py` with a class extending `BaseScraper`
2. Implement `_primary_extract(url, session)` returning `ScrapedMedia | None`
3. Add the class to the `SCRAPERS` list in `src/scrapers/__init__.py`
4. Add URL pattern to `src/utils/link_detector.py`

### Important Implementation Details

- **Signed URLs**: TikTok/Instagram/Facebook use temporary signed URLs — yt-dlp must download the full pipeline (can't just extract URL and download separately)
- **Pre-downloaded data**: Some scrapers populate `MediaItem.data` with bytes directly; others return URLs for `media_handler` to download later
- **All I/O is async**: Uses `aiohttp` for HTTP, `aiogram` for Telegram, semaphore-throttled concurrent downloads
- **Structured logging**: `structlog` with JSON output in production, console in dev; contextual fields (platform, url, duration_ms, method)

### Testing Patterns

- Mock `aiohttp.ClientSession` for HTTP responses
- `conftest.py` sets dummy `TELEGRAM_BOT_TOKEN` so config doesn't fail
- Use `AsyncMock` for async method mocking
- Test fallback chains by making primary methods fail
