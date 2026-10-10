"""Bounded HTTP GETs, identity validation, and RFC-style robots matching.

No cookies, authentication, proxies, fingerprint rotation or challenge solving.
The robot policy is checked before the requested page and every redirect.
"""

from __future__ import annotations

import math
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .errors import SourceError

HOST = "www.stepstone.de"
USER_AGENT = "StepStoneMCP/0.1 (+https://github.com/ahmed-elkashef/stepstone-mcp)"
ROBOTS_URL = f"https://{HOST}/robots.txt"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def validate_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname == HOST
                 and parts.port in (None, 443) and not parts.username
                 and not parts.password and not parts.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid or any(ord(c) < 32 for c in url):
        raise SourceError("invalid_url", "Only public HTTPS www.stepstone.de URLs without credentials or fragments are accepted.", str(url))
    return url


def robots_allowed(text: str, url: str, agent: str = "StepStoneMCP") -> bool:
    """Longest matching rule wins; allow wins ties. Supports '*' and '$'."""
    groups = []
    agents, rules = [], []
    for line in text.splitlines() + ["User-agent: <end>"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in {"allow", "disallow"} and agents and value:
            rules.append((key == "allow", value))
    if agents and rules:
        groups.append((agents, rules))
    agent = agent.lower()
    matching = [(max((len(a) for a in names if a != "*" and a in agent), default=0), rules)
                for names, rules in groups if "*" in names or any(a in agent for a in names)]
    if not matching:
        return True
    specificity = max(n for n, _ in matching)
    parts = urlsplit(url)
    target = unquote(parts.path or "/") + ("?" + unquote(parts.query) if parts.query else "")
    matches = []
    for n, rules in matching:
        if n != specificity:
            continue
        for allowed, pattern in rules:
            end = pattern.endswith("$")
            body = pattern[:-1] if end else pattern
            regex = "^" + ".*".join(re.escape(part) for part in body.split("*")) + ("$" if end else "")
            if re.match(regex, target):
                matches.append((len(pattern.encode("utf-8")), allowed))
    return max(matches, default=(0, True))[1]


def retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            return max(0.0, (instant - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class PublicTransport:
    def __init__(self, *, timeout: float = 15, client: httpx.Client | None = None,
                 min_interval: float = 1, robots_ttl: float = 300, backend: str = "httpx"):
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        if min_interval < 0 or robots_ttl <= 0:
            raise ValueError("Invalid throttle or robots cache duration")
        if backend not in {"httpx", "node"}:
            raise ValueError("backend must be httpx or node")
        self.backend = backend
        self.node = shutil.which("node") if backend == "node" else None
        if backend == "node" and not self.node:
            raise ValueError("Node.js 18 or newer is required for the selected node backend")
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)
        self.min_interval, self.robots_ttl = min_interval, robots_ttl
        self._last_request = 0.0
        self._robots_text: str | None = None
        self._robots_at = 0.0
        self._cooldown_until = 0.0

    def close(self):
        self.client.close()

    def _request(self, url: str) -> tuple[int, str, httpx.Headers]:
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            raise SourceError("rate_limited", "Provider Retry-After cooldown is still active; resume later.", url,
                              429, remaining)
        delay = self.min_interval - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()
        if self.backend == "node":
            try:
                completed = subprocess.run([self.node, str(Path(__file__).with_name("fetch.mjs")),
                    url, str(math.ceil(self.timeout * 1000)), USER_AGENT],
                    capture_output=True, text=True, timeout=self.timeout + 2)
                response = json.loads(completed.stdout)
            except subprocess.TimeoutExpired as exc:
                raise SourceError("timeout", "Provider request timed out; resume the same page.", url) from exc
            except (OSError, ValueError) as exc:
                raise SourceError("network_error", "Selected HTTP backend failed; no alternate access attempted.", url,
                                  details={"exception_type": type(exc).__name__}) from exc
            if response.get("error"):
                raise SourceError(response["error"], "Selected HTTP backend could not retrieve the provider response.", url,
                                  details={"exception_type": response.get("exception_type")})
            if completed.returncode or not isinstance(response.get("status"), int) or not isinstance(response.get("body"), str):
                raise SourceError("invalid_response", "HTTP backend returned invalid response metadata.", url)
            return response["status"], response["body"], httpx.Headers(response.get("headers", {}))
        try:
            deadline = time.monotonic() + self.timeout
            with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout) as response:
                chunks, length = [], 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if time.monotonic() > deadline:
                        raise SourceError("timeout", "Response exceeded the request time budget.", url)
                    if length > MAX_RESPONSE_BYTES:
                        raise SourceError("response_too_large", "Response exceeds the 8 MiB limit; no truncated result returned.", url)
                    chunks.append(chunk)
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                return response.status_code, body, response.headers
        except httpx.TimeoutException as exc:
            raise SourceError("timeout", "Provider request timed out; resume the same page.", url) from exc
        except httpx.HTTPError as exc:
            raise SourceError("network_error", "Provider request failed; resume the same page.", url,
                              details={"exception_type": type(exc).__name__}) from exc

    def policy(self) -> str:
        if self._robots_text is not None and time.monotonic() - self._robots_at < self.robots_ttl:
            return self._robots_text
        status, body, headers = self._request(ROBOTS_URL)
        if status != 200 or "user-agent:" not in body.lower():
            if status == 429:
                wait = retry_after(headers.get("retry-after"))
                self._cooldown_until = time.monotonic() + (wait or 60)
            raise SourceError("robots_unavailable", "Robot policy could not be verified; no page request made.",
                              ROBOTS_URL, status, retry_after(headers.get("retry-after")))
        self._robots_text, self._robots_at = body, time.monotonic()
        return body

    def get(self, url: str) -> tuple[str, str]:
        validate_url(url)
        policy = self.policy()
        for _ in range(4):
            validate_url(url)
            if not robots_allowed(policy, url):
                raise SourceError("robots_denied", "Current provider robot policy prohibits this URL; no page request made.",
                                  url, details={"policy_url": ROBOTS_URL})
            status, body, headers = self._request(url)
            if status in {301, 302, 303, 307, 308}:
                destination = headers.get("location")
                if not destination:
                    raise SourceError("invalid_redirect", "Redirect has no destination.", url, status)
                url = urljoin(url, destination)
                continue
            if status == 429:
                wait = retry_after(headers.get("retry-after"))
                self._cooldown_until = time.monotonic() + (wait or 60)
                raise SourceError("rate_limited", "Provider rate limit; stop and resume the same page after Retry-After.",
                                  url, status, wait)
            if status in {401, 403}:
                raise SourceError("provider_blocked", "Provider denied access; no bypass or automatic retry attempted.", url, status)
            if status != 200:
                raise SourceError("http_error", "Provider returned a non-success status; not an empty result.", url, status)
            if any(phrase in body[:20000].lower() for phrase in ("verify you are human", "just a moment...", "access denied", "captcha challenge")):
                raise SourceError("provider_challenge", "Provider challenge detected; no challenge solving attempted.", url, status)
            return body, url
        raise SourceError("redirect_limit", "Provider exceeded the redirect limit.", url)
