from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.scrapers.facebook import FacebookScraper
from src.scrapers.github import GitHubScraper
from src.scrapers.instagram import InstagramScraper
from src.scrapers.reddit import RedditScraper
from src.scrapers.tiktok import TikTokScraper
from src.scrapers.twitter import TwitterScraper
from src.scrapers.youtube import YouTubeScraper

SCRAPERS: list[type[BaseScraper]] = [
    TwitterScraper,
    YouTubeScraper,
    InstagramScraper,
    TikTokScraper,
    FacebookScraper,
    GitHubScraper,
    RedditScraper,
]

__all__ = [
    "BaseScraper",
    "ScrapedMedia",
    "MediaItem",
    "MediaType",
    "SCRAPERS",
    "TwitterScraper",
    "YouTubeScraper",
    "InstagramScraper",
    "TikTokScraper",
    "FacebookScraper",
    "GitHubScraper",
    "RedditScraper",
]
