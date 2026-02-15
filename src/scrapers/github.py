from __future__ import annotations

import re

import aiohttp

from src.scrapers.base import BaseScraper, ScrapedMedia
from src.utils.link_detector import Platform


class GitHubScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.GITHUB

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Fetch commit or pull request info via the GitHub API."""
        commit_match = re.search(
            r"github\.com/([\w\-]+)/([\w\-]+)/commit/([0-9a-f]+)", url
        )
        pr_match = re.search(
            r"github\.com/([\w\-]+)/([\w\-]+)/pull/(\d+)", url
        )

        if commit_match:
            return await self._extract_commit(url, *commit_match.groups())
        elif pr_match:
            return await self._extract_pull_request(url, *pr_match.groups())
        else:
            raise ValueError(f"Could not parse GitHub URL: {url}")

    async def _extract_commit(
        self, url: str, owner: str, repo: str, sha: str
    ) -> ScrapedMedia:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        commit = data.get("commit", {})
        author = commit.get("author", {}).get("name", "Unknown")
        message = commit.get("message", "")

        stats = data.get("stats", {})
        files = data.get("files", [])

        lines = [
            f"Commit: {sha[:8]}",
            f"Author: {author}",
            f"Message: {message}",
            "",
            f"+{stats.get('additions', 0)} -{stats.get('deletions', 0)} "
            f"in {len(files)} file(s)",
        ]
        for f in files[:10]:
            lines.append(f"  {f.get('status', '?')} {f.get('filename', '')}")

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
            caption="\n".join(lines),
            media_items=[],
        )

    async def _extract_pull_request(
        self, url: str, owner: str, repo: str, pr_number: str
    ) -> ScrapedMedia:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        author = data.get("user", {}).get("login", "Unknown")
        title = data.get("title", "")
        body = data.get("body", "") or ""
        state = data.get("state", "unknown")
        additions = data.get("additions", 0)
        deletions = data.get("deletions", 0)
        changed_files = data.get("changed_files", 0)
        merged = data.get("merged", False)

        status = "merged" if merged else state

        lines = [
            f"PR #{pr_number}: {title}",
            f"Author: {author} | Status: {status}",
            f"+{additions} -{deletions} in {changed_files} file(s)",
        ]
        if body:
            # Truncate long PR bodies
            body_preview = body[:300]
            if len(body) > 300:
                body_preview += "..."
            lines.append("")
            lines.append(body_preview)

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
            caption="\n".join(lines),
            media_items=[],
        )
