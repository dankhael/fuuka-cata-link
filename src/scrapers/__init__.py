from src.scrapers.base import BaseScraper, ScrapedMedia, MediaItem, MediaType
from src.scrapers.twitter import TwitterScraper
from src.scrapers.youtube import YouTubeScraper
from src.scrapers.instagram import InstagramScraper
from src.scrapers.tiktok import TikTokScraper
from src.scrapers.facebook import FacebookScraper
from src.scrapers.github import GitHubScraper
from src.scrapers.reddit import RedditScraper

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
