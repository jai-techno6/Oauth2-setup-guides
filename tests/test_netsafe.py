from __future__ import annotations

import asyncio

import pytest

from app.agent.netsafe import UnsafeURL, assert_public_url, html_to_text


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "ftp://8.8.8.8/",
    "https://8.8.8.8:8443/",
    "https://user:pass@8.8.8.8/",
    "file:///etc/passwd",
])
def test_blocks_non_public_targets(url):
    with pytest.raises(UnsafeURL):
        asyncio.run(assert_public_url(url))


def test_allows_public_ip_literal():
    asyncio.run(assert_public_url("https://8.8.8.8/"))  # IP literal: no DNS lookup needed


def test_html_to_text_strips_scripts_and_tags():
    text = html_to_text("<html><script>var a=1;</script><style>p{}</style><p>Hello&nbsp;<b>world</b></p></html>")
    assert text == "Hello world"
