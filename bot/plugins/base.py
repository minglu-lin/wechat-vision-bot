#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base Plugin Interface for WeChat Bot
All plugins implement this interface for both:
1. OpenAI-compatible Function Calling (LLM autonomous invocation)
2. Fast-Path direct regex execution (0-token, zero-latency direct reply)
"""

import ipaddress
import json
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class UnsafeURLError(ValueError):
    """Raised when a URL targets a disallowed scheme or private/internal address."""


def validate_public_url(url: str) -> str:
    """Validate that a URL uses http/https and resolves strictly to global public IPs."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("Empty URL")
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme}")
    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeURLError("Missing URL hostname")
    if host.lower() in ("localhost", "localhost.localdomain"):
        raise UnsafeURLError("Loopback host blocked")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as e:
        raise UnsafeURLError(f"DNS resolution failed for {host}: {e}")

    if not infos:
        raise UnsafeURLError(f"No DNS records for {host}")

    for info in infos:
        ip_str = info[4][0]
        # Strip IPv6 scope id if present
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        ip_obj = ipaddress.ip_address(ip_str)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
            or not ip_obj.is_global
        ):
            raise UnsafeURLError(f"Blocked non-public address: {ip_obj}")
    return url.strip()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects: int = 5):
        super().__init__()
        self._redirects = 0
        self._max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._redirects += 1
        if self._redirects > self._max_redirects:
            raise UnsafeURLError("Too many HTTP redirects")
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        validate_public_url(resolved)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def safe_http_get_bytes(
    url: str,
    headers: dict = None,
    timeout: int = 10,
    max_bytes: int = 6 * 1024 * 1024,
) -> tuple:
    """SSRF-safe HTTP(S) GET with redirect IP re-validation and response byte cap.
    Returns (data_bytes, content_type, final_url).
    """
    clean_url = validate_public_url(url)
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    if headers:
        req_headers.update(headers)
    opener = urllib.request.build_opener(_SafeRedirectHandler(max_redirects=5))
    req = urllib.request.Request(clean_url, headers=req_headers)
    with opener.open(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        content_len = resp.headers.get("Content-Length")
        if content_len:
            try:
                if int(content_len) > max_bytes:
                    raise ValueError(f"Response too large ({content_len} > {max_bytes} bytes)")
            except ValueError as ve:
                if "Response too large" in str(ve):
                    raise
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Response exceeded maximum size of {max_bytes} bytes")
        return data, content_type, resp.geturl()


def atomic_write_json(path: str, data: dict, mode: int = 0o600):
    """Atomically write JSON via temp file + os.replace to prevent corruption."""
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".cfg_", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None and os.name != "nt":
            try:
                os.chmod(tmp_path, mode)
            except Exception:
                pass
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


class BasePlugin:
    name: str = ""
    description: str = ""
    parameters: dict = {}
    fast_patterns: list = []  # List of regex pattern strings for fast matching

    def to_openai_tool(self) -> dict:
        """Export OpenAI-compatible function calling tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

    def match_fast_path(self, text: str):
        """Check if user query matches any fast path regex pattern.
        Returns kwargs dict if matched, None otherwise.
        """
        if not self.fast_patterns or not text:
            return None
        t = text.strip()
        for pat in self.fast_patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return m.groupdict()
        return None

    def execute(self, **kwargs) -> dict:
        """Execute the API call and return structured data dict."""
        raise NotImplementedError

    def format_fast_reply(self, result: dict) -> str:
        """Format the execution result into plain text for WeChat direct fast-path reply."""
        raise NotImplementedError
