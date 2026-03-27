"""Benchmark test URLs organized by platform and size category.

These must be real, publicly accessible URLs. Uncomment and replace
placeholder URLs with actual content of the target size categories.

Size categories:
  - small:  Short content, single image or < 30s video
  - medium: Multi-image post or 30s-2min video
  - large:  Long video (2-5min) or large image carousel (5+ images)
"""

# ---------------------------------------------------------------------------
# YouTube (only Shorts / youtu.be links are detected by link_detector)
# ---------------------------------------------------------------------------

YOUTUBE_URLS: dict[str, list[str]] = {
    "small": [
        # Short YouTube Shorts (< 15 seconds)
        "https://www.youtube.com/shorts/EauqkXmKgIQ",
    ],
    "medium": [
        # Medium YouTube Shorts (15-30 seconds)
        "https://youtube.com/shorts/LfyCoNtKcHk?si=VlLXwCtSeNklRDAJ",
    ],
    "large": [
        # Longer YouTube Shorts / videos (30-60 seconds)
        "https://youtu.be/wYWggSuxwIo?si=yPDLoX0Iwp5P9KbP",
    ],
}

# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------

TWITTER_URLS: dict[str, list[str]] = {
    "small": [
        # Single image tweet
        "https://x.com/i/status/2036936847029657820",
    ],
    "medium": [
        # Multi-image tweet (2-4 images) or short video
        "https://x.com/i/status/2036485948301533563",
    ],
    "large": [
        # Long video tweet (1-3 min)
        "https://x.com/l4ert3s/status/2036923172927217870",
    ],
    "extra_large": [
        # Very long video tweet (1-3 min)
        "https://x.com/forumbunklr/status/2036990283267674329?s=20",
    ],
}

# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

TIKTOK_URLS: dict[str, list[str]] = {
    "small": [
        # Short TikTok (< 15s) or single photo
        "https://vt.tiktok.com/ZSmWp5csc/",
    ],
    "medium": [
        # Medium TikTok (15-60s) or photo carousel (3-5 photos)
        "https://vt.tiktok.com/ZSmPPSUxk/",
    ],
    "large": [
        # Long TikTok (1-3 min) or large photo carousel (6+ photos)
        "https://vt.tiktok.com/ZSaeh4gup/",
    ],
}

# ---------------------------------------------------------------------------
# Instagram (may require cookies_file in .env for some content)
# ---------------------------------------------------------------------------

INSTAGRAM_URLS: dict[str, list[str]] = {
    "small": [
        # Single image post
        # "https://www.instagram.com/p/EXAMPLE/",
    ],
    "medium": [
        # Carousel post (2-4 items)
        # "https://www.instagram.com/p/EXAMPLE/",
    ],
    "large": [
        # Reel or large carousel (5+ items)
        # "https://www.instagram.com/reel/EXAMPLE/",
    ],
}

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

REDDIT_URLS: dict[str, list[str]] = {
    "small": [
        # Image post
        # "https://www.reddit.com/r/subreddit/comments/EXAMPLE/title/",
    ],
    "medium": [
        # Short video or gallery post
        # "https://www.reddit.com/r/subreddit/comments/EXAMPLE/title/",
    ],
    "large": [
        # Long video post
        # "https://www.reddit.com/r/subreddit/comments/EXAMPLE/title/",
    ],
}
