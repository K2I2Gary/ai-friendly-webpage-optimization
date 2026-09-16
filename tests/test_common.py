"""Unit tests for the shared utilities in common.py."""

from common import extract_urls, is_url, url_to_filename


def test_url_to_filename_strips_query_and_fragment():
    assert url_to_filename("https://example.com/a?b=c#d") == "example.com_a"


def test_url_to_filename_domain_only():
    assert url_to_filename("https://example.com") == "example.com"


def test_is_url():
    assert is_url("https://x.com")
    assert is_url("http://x.com")
    assert not is_url("test.html")


def test_extract_urls():
    html = '<a href="/a">x</a><img src="/b.png"><form action="/c"></form>'
    assert extract_urls(html) == {"/a", "/b.png", "/c"}


def test_extract_urls_case_insensitive():
    assert extract_urls('<A HREF="/x"><IMG SRC="/y">') == {"/x", "/y"}
