# -*- coding: utf-8 -*-
"""RFC 6265 CookieJar for the internal browser engine.

Derived from vendored Obscura crate `obscura-net` (`crates/obscura-net/src/cookies.rs`,
commit `bd9529418b7fa975b50eabeac415cfaaa865a588`, Apache-2.0).
"""

from __future__ import annotations

import email.utils
import ipaddress
import json
import os
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_COOKIE_HEADER_BYTES = 4096
MAX_COOKIES_PER_DOMAIN = 50
MAX_COOKIES_TOTAL = 3000


def _now_unix() -> int:
    return int(time.time())


def _is_ip_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def domain_matches(request_host: str, cookie_domain: str, host_only: bool) -> bool:
    """RFC 6265 Section 5.1.3 domain matching."""
    req = request_host.lower().rstrip(".")
    dom = cookie_domain.lower().lstrip(".").rstrip(".")
    if req == dom:
        return True
    if host_only:
        return False
    if _is_ip_host(req):
        return False
    return req.endswith("." + dom)


def default_path(request_path: str) -> str:
    """RFC 6265 Section 5.1.4 default-path algorithm."""
    if not request_path or not request_path.startswith("/"):
        return "/"
    last_slash = request_path.rfind("/")
    if last_slash <= 0:
        return "/"
    return request_path[:last_slash]


def path_matches(request_path: str, cookie_path: str) -> bool:
    """RFC 6265 Section 5.1.4 path matching."""
    req = request_path or "/"
    cpath = cookie_path or "/"
    if req == cpath:
        return True
    if req.startswith(cpath):
        if cpath.endswith("/"):
            return True
        if len(req) > len(cpath) and req[len(cpath)] == "/":
            return True
    return False


def _parse_expires(raw: str) -> Optional[int]:
    try:
        dt = email.utils.parsedate_to_datetime(raw.strip())
        return int(dt.timestamp())
    except Exception:
        return None


@dataclass
class StoredCookie:
    name: str
    value: str
    domain: str
    host_only: bool = True
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: str = "Lax"
    expires: Optional[int] = None
    creation_time: int = field(default_factory=_now_unix)
    last_access_time: int = field(default_factory=_now_unix)
    persistent: bool = False

    def is_expired(self, now: Optional[int] = None) -> bool:
        if self.expires is None:
            return False
        current = _now_unix() if now is None else now
        return self.expires <= current

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoredCookie":
        return cls(
            name=str(data.get("name", "")),
            value=str(data.get("value", "")),
            domain=str(data.get("domain", "")).lower().lstrip("."),
            host_only=bool(data.get("host_only", True)),
            path=str(data.get("path") or "/"),
            secure=bool(data.get("secure", False)),
            http_only=bool(data.get("http_only", False)),
            same_site=str(data.get("same_site") or "Lax"),
            expires=int(data["expires"]) if data.get("expires") is not None else None,
            creation_time=int(data.get("creation_time") or _now_unix()),
            last_access_time=int(data.get("last_access_time") or _now_unix()),
            persistent=bool(data.get("persistent", data.get("expires") is not None)),
        )


class CookieJar:
    """Thread-safe RFC 6265 cookie jar matching `obscura_net::CookieJar`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cookies: List[StoredCookie] = []

    def clear(self) -> None:
        with self._lock:
            self._cookies.clear()

    def _evict_expired(self, now: Optional[int] = None) -> None:
        current = _now_unix() if now is None else now
        self._cookies = [c for c in self._cookies if not c.is_expired(current)]

    def insert_from_header(
        self,
        raw_header: str,
        request_url: str,
        *,
        from_http: bool = True,
    ) -> bool:
        """Parse and store one `Set-Cookie` (or `document.cookie`) string."""
        if not raw_header or len(raw_header.encode("utf-8", errors="ignore")) > MAX_COOKIE_HEADER_BYTES:
            return False

        parsed_url = urllib.parse.urlsplit(request_url)
        scheme = (parsed_url.scheme or "").lower()
        req_host = (parsed_url.hostname or "").lower().rstrip(".")
        req_path = parsed_url.path or "/"
        if not req_host:
            return False

        is_secure_origin = scheme == "https" or req_host in ("localhost", "127.0.0.1", "::1")

        parts = [p.strip() for p in raw_header.split(";")]
        if not parts or "=" not in parts[0]:
            return False
        name_val = parts[0]
        eq_idx = name_val.find("=")
        name = name_val[:eq_idx].strip()
        value = name_val[eq_idx + 1 :].strip()
        if not name or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
            return False

        now = _now_unix()
        domain_attr: Optional[str] = None
        path_attr: Optional[str] = None
        secure = False
        http_only = False
        same_site = "Lax"
        max_age: Optional[int] = None
        expires: Optional[int] = None

        for attr in parts[1:]:
            if not attr:
                continue
            if "=" in attr:
                k, v = attr.split("=", 1)
                k_low = k.strip().lower()
                v_clean = v.strip()
                if k_low == "domain":
                    if v_clean:
                        domain_attr = v_clean.lower().lstrip(".").rstrip(".")
                elif k_low == "path":
                    if v_clean.startswith("/"):
                        path_attr = v_clean
                elif k_low == "max-age":
                    try:
                        max_age = int(v_clean)
                    except ValueError:
                        pass
                elif k_low == "expires":
                    expires = _parse_expires(v_clean)
                elif k_low == "samesite":
                    ss_low = v_clean.lower()
                    if ss_low == "strict":
                        same_site = "Strict"
                    elif ss_low == "none":
                        same_site = "None"
                    else:
                        same_site = "Lax"
            else:
                flag = attr.strip().lower()
                if flag == "secure":
                    secure = True
                elif flag == "httponly":
                    http_only = True

        # Non-HTTP callers (document.cookie) cannot set or overwrite HttpOnly cookies
        if not from_http and http_only:
            return False

        # Cookie prefixes (RFC 6265bis): __Secure- and __Host-
        if name.startswith("__Secure-"):
            if not (secure and is_secure_origin):
                return False
        if name.startswith("__Host-"):
            if not (secure and is_secure_origin and domain_attr is None and (path_attr in (None, "/"))):
                return False

        if same_site == "None" and not secure:
            return False
        if secure and not is_secure_origin:
            return False

        if domain_attr is not None:
            if not domain_matches(req_host, domain_attr, host_only=False):
                return False
            cookie_domain = domain_attr
            host_only = False
        else:
            cookie_domain = req_host
            host_only = True

        cookie_path = path_attr if path_attr is not None else default_path(req_path)

        effective_expires: Optional[int] = None
        persistent = False
        if max_age is not None:
            persistent = True
            effective_expires = now + max_age if max_age > 0 else 0
        elif expires is not None:
            persistent = True
            effective_expires = expires

        with self._lock:
            self._evict_expired(now)
            # Check for existing cookie with same (name, domain, path)
            existing_idx: Optional[int] = None
            for idx, existing in enumerate(self._cookies):
                if (
                    existing.name == name
                    and existing.domain == cookie_domain
                    and existing.path == cookie_path
                ):
                    if not from_http and existing.http_only:
                        return False
                    existing_idx = idx
                    break

            if effective_expires is not None and effective_expires <= now:
                if existing_idx is not None:
                    self._cookies.pop(existing_idx)
                return True

            creation_time = (
                self._cookies[existing_idx].creation_time
                if existing_idx is not None
                else now
            )
            new_cookie = StoredCookie(
                name=name,
                value=value,
                domain=cookie_domain,
                host_only=host_only,
                path=cookie_path,
                secure=secure,
                http_only=http_only,
                same_site=same_site,
                expires=effective_expires,
                creation_time=creation_time,
                last_access_time=now,
                persistent=persistent,
            )
            if existing_idx is not None:
                self._cookies[existing_idx] = new_cookie
            else:
                # Enforce per-domain and global caps
                domain_cookies = [
                    i for i, c in enumerate(self._cookies) if c.domain == cookie_domain
                ]
                if len(domain_cookies) >= MAX_COOKIES_PER_DOMAIN:
                    self._cookies.pop(domain_cookies[0])
                if len(self._cookies) >= MAX_COOKIES_TOTAL:
                    self._cookies.pop(0)
                self._cookies.append(new_cookie)
        return True

    def set_cookie(
        self,
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        *,
        secure: bool = False,
        http_only: bool = False,
        same_site: str = "Lax",
        expires: Optional[int] = None,
    ) -> None:
        """Directly insert/replace a cookie (used by MCP `browser_set_cookie`)."""
        clean_domain = domain.strip().lower().lstrip(".").rstrip(".")
        if not clean_domain or not name:
            return
        now = _now_unix()
        with self._lock:
            self._evict_expired(now)
            self._cookies = [
                c
                for c in self._cookies
                if not (c.name == name and c.domain == clean_domain and c.path == (path or "/"))
            ]
            if expires is not None and expires <= now:
                return
            self._cookies.append(
                StoredCookie(
                    name=name,
                    value=value,
                    domain=clean_domain,
                    host_only=not domain.strip().startswith("."),
                    path=path or "/",
                    secure=secure,
                    http_only=http_only,
                    same_site=same_site or "Lax",
                    expires=expires,
                    creation_time=now,
                    last_access_time=now,
                    persistent=expires is not None,
                )
            )

    def matching_cookies(
        self,
        request_url: str,
        *,
        for_js: bool = False,
    ) -> List[StoredCookie]:
        """Return cookies matching `request_url` sorted per RFC 6265 Section 5.4."""
        parsed = urllib.parse.urlsplit(request_url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path or "/"
        if not host:
            return []
        is_secure = scheme == "https" or host in ("localhost", "127.0.0.1", "::1")
        now = _now_unix()
        with self._lock:
            self._evict_expired(now)
            matched: List[StoredCookie] = []
            for cookie in self._cookies:
                if cookie.secure and not is_secure:
                    continue
                if for_js and cookie.http_only:
                    continue
                if not domain_matches(host, cookie.domain, cookie.host_only):
                    continue
                if not path_matches(path, cookie.path):
                    continue
                cookie.last_access_time = now
                matched.append(cookie)
            matched.sort(key=lambda c: (-len(c.path), c.creation_time))
            return matched

    def cookie_header(self, request_url: str) -> str:
        """RFC 6265 `Cookie` request header for `request_url`."""
        cookies = self.matching_cookies(request_url, for_js=False)
        return "; ".join(f"{c.name}={c.value}" for c in cookies)

    def cookie_header_for_js(self, request_url: str) -> str:
        """`document.cookie` string (excludes `HttpOnly` cookies)."""
        cookies = self.matching_cookies(request_url, for_js=True)
        return "; ".join(f"{c.name}={c.value}" for c in cookies)

    def all_cookies(self, request_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all active cookies (or those matching `request_url` if given)."""
        with self._lock:
            self._evict_expired()
            if request_url:
                return [c.to_dict() for c in self.matching_cookies(request_url, for_js=False)]
            return [c.to_dict() for c in self._cookies]

    def load_dicts(self, items: List[Dict[str, Any]]) -> int:
        now = _now_unix()
        loaded = 0
        with self._lock:
            for item in items:
                if not isinstance(item, dict) or not item.get("name") or not item.get("domain"):
                    continue
                cookie = StoredCookie.from_dict(item)
                if not cookie.is_expired(now):
                    self._cookies = [
                        c
                        for c in self._cookies
                        if not (
                            c.name == cookie.name
                            and c.domain == cookie.domain
                            and c.path == cookie.path
                        )
                    ]
                    self._cookies.append(cookie)
                    loaded += 1
        return loaded

    def save_to_file(self, path: Path) -> None:
        """Atomically persist cookies to `path` with owner-only (0600) permissions."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            self._evict_expired()
            payload = json.dumps([c.to_dict() for c in self._cookies], indent=2)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def load_from_file(self, path: Path) -> int:
        if not path.is_file():
            return 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if not isinstance(raw, list):
            return 0
        return self.load_dicts(raw)
