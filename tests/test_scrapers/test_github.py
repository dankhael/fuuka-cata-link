import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.scrapers.github import GitHubScraper
from src.utils.link_detector import Platform


@pytest.mark.asyncio
async def test_github_commit_extraction():
    mock_data = {
        "commit": {
            "author": {"name": "Dev"},
            "message": "Fix bug #42",
        },
        "stats": {"additions": 10, "deletions": 3},
        "files": [
            {"status": "modified", "filename": "main.py"},
            {"status": "added", "filename": "test.py"},
        ],
    }

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=mock_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = GitHubScraper()
        result = await scraper._primary_extract(
            "https://github.com/owner/repo/commit/abc123def"
        )

    assert result.platform == Platform.GITHUB
    assert result.author == "Dev"
    assert "Fix bug #42" in result.caption
    assert "+10 -3" in result.caption
    assert result.has_media is False


@pytest.mark.asyncio
async def test_github_pr_extraction():
    mock_data = {
        "user": {"login": "contributor"},
        "title": "Add new feature",
        "body": "This PR adds a cool new feature.",
        "state": "open",
        "merged": False,
        "additions": 50,
        "deletions": 10,
        "changed_files": 5,
    }

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=mock_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = GitHubScraper()
        result = await scraper._primary_extract(
            "https://github.com/owner/repo/pull/42"
        )

    assert result.platform == Platform.GITHUB
    assert result.author == "contributor"
    assert "PR #42" in result.caption
    assert "Add new feature" in result.caption
    assert "open" in result.caption
    assert "+50 -10" in result.caption


@pytest.mark.asyncio
async def test_github_invalid_url():
    scraper = GitHubScraper()
    with pytest.raises(ValueError, match="Could not parse"):
        await scraper._primary_extract("https://github.com/owner/repo/issues/1")
