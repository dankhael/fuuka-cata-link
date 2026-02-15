import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.opengraph import fetch_opengraph, OpenGraphData, download_og_image


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta property="og:image" content="https://example.com/image.jpg" />
<meta property="og:title" content="Test Post Title" />
<meta property="og:description" content="A description of the post" />
<meta property="og:site_name" content="Instagram" />
</head>
<body></body>
</html>
"""

SAMPLE_HTML_REVERSED = """
<head>
<meta content="https://example.com/photo.jpg" property="og:image" />
<meta content="Reversed Order" property="og:title" />
</head>
"""


@pytest.mark.asyncio
async def test_fetch_opengraph_standard():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = AsyncMock(return_value=SAMPLE_HTML)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.utils.opengraph.aiohttp.ClientSession", return_value=mock_session):
        og = await fetch_opengraph("https://instagram.com/p/test")

    assert og.image == "https://example.com/image.jpg"
    assert og.title == "Test Post Title"
    assert og.description == "A description of the post"
    assert og.site_name == "Instagram"


@pytest.mark.asyncio
async def test_fetch_opengraph_reversed_attrs():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = AsyncMock(return_value=SAMPLE_HTML_REVERSED)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.utils.opengraph.aiohttp.ClientSession", return_value=mock_session):
        og = await fetch_opengraph("https://facebook.com/share/p/test")

    assert og.image == "https://example.com/photo.jpg"
    assert og.title == "Reversed Order"


@pytest.mark.asyncio
async def test_fetch_opengraph_no_tags():
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = AsyncMock(return_value="<html><body>No og tags</body></html>")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.utils.opengraph.aiohttp.ClientSession", return_value=mock_session):
        og = await fetch_opengraph("https://example.com")

    assert og.image is None
    assert og.title is None


@pytest.mark.asyncio
async def test_download_og_image_success():
    og = OpenGraphData(image="https://example.com/img.jpg")

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.read = AsyncMock(return_value=b"image_bytes")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.utils.opengraph.aiohttp.ClientSession", return_value=mock_session):
        data = await download_og_image(og)

    assert data == b"image_bytes"


@pytest.mark.asyncio
async def test_download_og_image_no_url():
    og = OpenGraphData(image=None)
    data = await download_og_image(og)
    assert data is None
