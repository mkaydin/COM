"""Tests for outbound URL validation.

Divination fetches URLs that come out of search results. Without a check, a
hostile or hijacked result can make the backend reach the local Postgres, the
Ollama server, or a cloud metadata endpoint — and Lightpanda executes the page
in a real browser.
"""
import pytest

from agents.url_guard import ensure_public_url, UnsafeURLError, _is_blocked_ip
import ipaddress


class TestBlockedHosts:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/",
        "http://127.0.0.1:5432/",
        "http://localhost/",
        "http://localhost:11434/v1",
        "http://[::1]/",
        "http://0.0.0.0/",
    ])
    def test_loopback(self, url):
        with pytest.raises(UnsafeURLError):
            ensure_public_url(url)

    @pytest.mark.parametrize("url", [
        "http://10.0.0.5/",
        "http://192.168.50.1:1234/v1",
        "http://172.16.0.1/",
        "http://192.168.1.1/admin",
    ])
    def test_rfc1918(self, url):
        with pytest.raises(UnsafeURLError):
            ensure_public_url(url)

    def test_cloud_metadata(self):
        with pytest.raises(UnsafeURLError) as exc:
            ensure_public_url("http://169.254.169.254/latest/meta-data/iam/")
        assert "169.254.169.254" in str(exc.value)

    @pytest.mark.parametrize("url", [
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[fd00:ec2::254]/",
    ])
    def test_link_local_and_ula_ipv6(self, url):
        with pytest.raises(UnsafeURLError):
            ensure_public_url(url)


class TestBlockedSchemes:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes(self, url):
        with pytest.raises(UnsafeURLError):
            ensure_public_url(url)

    def test_missing_host(self):
        with pytest.raises(UnsafeURLError):
            ensure_public_url("http:///path")

    def test_unresolvable_host(self):
        with pytest.raises(UnsafeURLError):
            ensure_public_url("http://this-host-does-not-exist.invalid/")


class TestAllowedUrls:
    @pytest.mark.parametrize("url", [
        "https://example.com/",
        "http://example.com/page?q=1",
        "https://en.wikipedia.org/wiki/Main_Page",
        "https://8.8.8.8/",
    ])
    def test_public_urls_pass(self, url):
        assert ensure_public_url(url) == url


class TestIsBlockedIp:
    @pytest.mark.parametrize("addr,blocked", [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("192.168.0.1", True),
        ("172.20.0.1", True),
        ("169.254.169.254", True),
        ("0.0.0.0", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("2606:4700::1111", False),
    ])
    def test_ranges(self, addr, blocked):
        assert _is_blocked_ip(ipaddress.ip_address(addr)) is blocked

    def test_ipv4_mapped_ipv6_is_unwrapped(self):
        """::ffff:127.0.0.1 must not sneak past as a "public" IPv6 address."""
        assert _is_blocked_ip(ipaddress.ip_address("::ffff:127.0.0.1")) is True

    def test_multicast_and_reserved(self):
        assert _is_blocked_ip(ipaddress.ip_address("224.0.0.1")) is True
        assert _is_blocked_ip(ipaddress.ip_address("240.0.0.1")) is True
