import pytest

from src.utils.link_detector import Platform, detect_links


class TestDetectLinks:
    def test_twitter_url(self):
        links = detect_links("Check this https://twitter.com/user/status/12345")
        assert len(links) == 1
        assert links[0].platform == Platform.TWITTER
        assert links[0].url == "https://twitter.com/user/status/12345"

    def test_x_dot_com_url(self):
        links = detect_links("Look https://x.com/user/status/99999")
        assert len(links) == 1
        assert links[0].platform == Platform.TWITTER

    def test_youtube_shorts(self):
        links = detect_links("https://youtube.com/shorts/abc123")
        assert len(links) == 1
        assert links[0].platform == Platform.YOUTUBE

    def test_instagram_reel(self):
        links = detect_links("https://www.instagram.com/reel/CxYz123/")
        assert len(links) == 1
        assert links[0].platform == Platform.INSTAGRAM

    def test_instagram_post(self):
        links = detect_links("https://instagram.com/p/ABC123/")
        assert len(links) == 1
        assert links[0].platform == Platform.INSTAGRAM

    def test_tiktok_url(self):
        links = detect_links("https://www.tiktok.com/@user/video/12345")
        assert len(links) == 1
        assert links[0].platform == Platform.TIKTOK

    def test_tiktok_vm_url(self):
        links = detect_links("https://vm.tiktok.com/ZMxyz/")
        assert len(links) == 1
        assert links[0].platform == Platform.TIKTOK

    def test_tiktok_vt_url(self):
        links = detect_links("https://vt.tiktok.com/ZSmjfk6rd/")
        assert len(links) == 1
        assert links[0].platform == Platform.TIKTOK
        assert links[0].url == "https://vt.tiktok.com/ZSmjfk6rd/"

    def test_tiktok_query_params_stripped(self):
        url = "https://www.tiktok.com/@nauticawithasix/video/7605860696445685022?q=nagi%20seishiro&t=1771151238662"
        links = detect_links(url)
        assert len(links) == 1
        assert links[0].platform == Platform.TIKTOK
        # Query params should be stripped
        assert "?" not in links[0].url
        assert links[0].url == "https://www.tiktok.com/@nauticawithasix/video/7605860696445685022"

    def test_facebook_url(self):
        links = detect_links("https://www.facebook.com/user/posts/12345")
        assert len(links) == 1
        assert links[0].platform == Platform.FACEBOOK

    def test_github_commit(self):
        links = detect_links(
            "https://github.com/owner/repo/commit/abc123def456"
        )
        assert len(links) == 1
        assert links[0].platform == Platform.GITHUB

    def test_github_pull_request(self):
        links = detect_links("https://github.com/owner/repo/pull/42")
        assert len(links) == 1
        assert links[0].platform == Platform.GITHUB
        assert links[0].url == "https://github.com/owner/repo/pull/42"

    def test_reddit_post(self):
        links = detect_links(
            "https://www.reddit.com/r/python/comments/abc123/some_title/"
        )
        assert len(links) == 1
        assert links[0].platform == Platform.REDDIT

    def test_reddit_share_link_params_stripped(self):
        url = (
            "https://www.reddit.com/r/BlueLock/comments/1r4ucte/"
            "whats_the_correlation_for_hioris_aura_being_ice/"
            "?utm_source=share&utm_medium=web3x&utm_name=web3xcss"
            "&utm_term=1&utm_content=share_button"
        )
        links = detect_links(url)
        assert len(links) == 1
        assert links[0].platform == Platform.REDDIT
        # All utm_ params should be stripped
        assert "utm_" not in links[0].url
        assert "share" not in links[0].url

    def test_multiple_links(self):
        text = (
            "Check these out: "
            "https://twitter.com/user/status/111 and "
            "https://www.instagram.com/p/ABC/"
        )
        links = detect_links(text)
        assert len(links) == 2
        platforms = {link.platform for link in links}
        assert platforms == {Platform.TWITTER, Platform.INSTAGRAM}

    def test_no_links(self):
        links = detect_links("Just a normal message with no links")
        assert links == []

    def test_unsupported_link(self):
        links = detect_links("https://example.com/something")
        assert links == []

    def test_deduplication(self):
        text = "https://x.com/user/status/123 https://x.com/user/status/123"
        links = detect_links(text)
        assert len(links) == 1

    def test_trailing_punctuation_stripped(self):
        links = detect_links("Check this: https://twitter.com/user/status/123!")
        assert links[0].url == "https://twitter.com/user/status/123"
