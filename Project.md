# Media Grabber Bot - Project Specification

## Overview
A modular Telegram bot that automatically extracts and reposts media (videos, images, text) from social media links shared by group members.

## Tech Stack
- **Language**: Python 3.11+
- **Bot Framework**: `aiogram` (async Telegram bot framework)
- **HTTP Client**: `aiohttp` (async HTTP requests)
- **Scraping**: 
  - `yt-dlp` (YouTube, TikTok, Instagram, Facebook videos)
  - `playwright` (fallback for complex scenarios)
  - Platform-specific APIs where available
- **Media Processing**: `Pillow` (image optimization)
- **Logging**: `structlog` (structured logging)
- **Testing**: `pytest`, `pytest-asyncio`, `pytest-mock`
- **Configuration**: `pydantic-settings` (type-safe config)
- **Database**: `aiosqlite` (optional, for caching/rate limiting)

## Supported Platforms
1. **Twitter/X** - tweets with images/videos
2. **YouTube Shorts** - short-form videos
3. **Instagram Reels** - reels
4. **Instagram Posts** - photos/carousels/videos
5. **TikTok** - videos
6. **Facebook Reels** - short videos
7. **Facebook Posts** - photos/videos
8. **GitHub Commits** - diff/code snippets
9. **Reddit Posts** - images/videos/text

## Core Requirements

### 1. Link Detection & Media Extraction
- Detect social media URLs in group messages automatically
- Extract all media (images, videos) from posts
- Support multiple media items per post (carousels, threads)
- Preserve original quality when possible
- Handle text-only posts appropriately

### 2. Message Formatting
- **Media posts**: Send media with caption + embedded source link at the end
- **Text-only posts**: Send text content without embedded link
- Include author/source information in caption
- Format: `[Caption/Description]\n\n🔗 Source: [original_link]`

### 3. Fallback Strategy (Multi-Method Fetching)
Each platform should implement a fallback chain:
1. **Primary method**: Platform-specific API or fastest scraper
2. **Secondary method**: `yt-dlp` (works for most video platforms)
3. **Tertiary method**: `playwright` headless browser (slowest, most reliable)
4. **Error handling**: Log failure and notify user if all methods fail

### 4. Performance
- Target response time: < 5 seconds for most requests
- Use async/await throughout for concurrent operations
- Implement connection pooling for HTTP requests
- Cache extracted media temporarily (optional)
- Rate limiting per user/group to prevent abuse

### 5. Modularity
```
telegram-media-bot/
├── src/
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers.py          # Message handlers
│   │   ├── filters.py           # Custom filters
│   │   └── middlewares.py       # Rate limiting, logging
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract base scraper
│   │   ├── twitter.py
│   │   ├── youtube.py
│   │   ├── instagram.py
│   │   ├── tiktok.py
│   │   ├── facebook.py
│   │   ├── github.py
│   │   └── reddit.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── link_detector.py     # URL extraction & platform detection
│   │   ├── media_handler.py     # Media download & processing
│   │   ├── formatters.py        # Message formatting
│   │   └── cache.py             # Optional caching layer
│   ├── config.py                # Configuration management
│   └── main.py                  # Entry point
├── tests/
│   ├── __init__.py
│   ├── test_scrapers/
│   │   ├── test_twitter.py
│   │   ├── test_youtube.py
│   │   └── ...
│   ├── test_utils/
│   │   ├── test_link_detector.py
│   │   └── test_formatters.py
│   └── fixtures/                # Mock responses
├── logs/                        # Log files
├── .env.example
├── requirements.txt
├── pyproject.toml              # Poetry/setuptools config
└── README.md
```

### 6. Logging Strategy
Use `structlog` for structured, searchable logs:

```python
# Log levels and contexts
- DEBUG: Detailed scraping steps, API calls
- INFO: Successful media extraction, bot events
- WARNING: Fallback method triggered, rate limits
- ERROR: Scraping failures, API errors
- CRITICAL: Bot crashes, configuration errors

# Log fields to include:
- timestamp
- platform (twitter, instagram, etc.)
- url (original link)
- method (api, yt-dlp, playwright)
- duration (execution time)
- media_count (number of items extracted)
- user_id, chat_id
- error_type (if applicable)
```

Example log output:
```json
{
  "event": "media_extracted",
  "timestamp": "2024-02-14T10:30:45Z",
  "platform": "instagram",
  "url": "https://instagram.com/p/abc123",
  "method": "yt-dlp",
  "duration_ms": 2340,
  "media_count": 3,
  "user_id": 12345,
  "chat_id": -67890
}
```

### 7. Testing Requirements

#### Unit Tests
- Test each scraper independently with mocked responses
- Test link detection with various URL formats
- Test message formatting logic
- Test fallback chain execution
- Aim for >80% code coverage

#### Integration Tests
- Test full flow: message → scraping → Telegram response
- Test error handling and fallbacks
- Test rate limiting

#### Test Fixtures
- Mock HTML/JSON responses for each platform
- Sample media files for processing tests

```python
# Example test structure
@pytest.mark.asyncio
async def test_instagram_reel_extraction():
    scraper = InstagramScraper()
    result = await scraper.extract("https://instagram.com/reel/xyz")
    assert result.media_type == "video"
    assert len(result.media_urls) == 1
    assert result.caption is not None

@pytest.mark.asyncio
async def test_fallback_chain():
    scraper = TwitterScraper()
    # Mock primary method failure
    with patch.object(scraper, '_api_method', side_effect=Exception):
        result = await scraper.extract("https://twitter.com/...")
        # Should succeed via fallback
        assert result.method_used == "yt-dlp"
```

## Configuration

### Environment Variables (.env)
```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
ALLOWED_CHATS=chat_id1,chat_id2  # Optional: whitelist

# API Keys (optional, for better reliability)
TWITTER_BEARER_TOKEN=optional
REDDIT_CLIENT_ID=optional
REDDIT_CLIENT_SECRET=optional

# Performance
MAX_FILE_SIZE_MB=50
DOWNLOAD_TIMEOUT_SECONDS=30
CONCURRENT_DOWNLOADS=3

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
```

## Implementation Phases

### Phase 1: Core Infrastructure
- [ ] Project setup with proper structure
- [ ] Telegram bot basic handlers
- [ ] Link detection utility
- [ ] Base scraper abstract class
- [ ] Logging configuration
- [ ] Unit test framework

### Phase 2: Platform Scrapers (Priority Order)
- [ ] Twitter scraper (high priority, frequently shared)
- [ ] Instagram posts/reels
- [ ] TikTok
- [ ] YouTube Shorts
- [ ] Reddit
- [ ] Facebook posts/reels
- [ ] GitHub commits

### Phase 3: Enhancements
- [ ] Fallback chain implementation
- [ ] Media caching layer
- [ ] Rate limiting middleware
- [ ] Error recovery and retry logic
- [ ] Performance monitoring

### Phase 4: Polish
- [ ] Comprehensive testing
- [ ] Documentation
- [ ] Deployment guide
- [ ] Admin commands (stats, health check)

## Success Criteria
- ✅ Successfully extracts media from 90%+ of supported links
- ✅ Average response time < 5 seconds
- ✅ Handles errors gracefully with informative messages
- ✅ 80%+ test coverage
- ✅ Clear logs for debugging
- ✅ Easy to add new platforms

## Notes & Considerations

### Legal/Ethical
- Respect robots.txt and rate limits
- Consider adding attribution/credits
- Be aware of platform ToS regarding scraping
- Implement user-agent rotation if needed

### Future Enhancements
- Admin dashboard for monitoring
- User command to force specific scraper method
- Media quality selection (HD/SD)
- Download history/statistics
- Multi-language support
- Scheduled content posting