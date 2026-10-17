# -*- coding: utf-8 -*-
"""SSRF deny-set and private-network opt-in for the internal browser engine.

Derived from vendored Obscura crate `obscura-ssrf` (`crates/obscura-ssrf/src/lib.rs`,
commit `bd9529418b7fa975b50eabeac415cfaaa865a588`, Apache-2.0) and integrated
with `iralens.security`.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
from typing import List, Tuple, Union

from ...errors import SecurityBlockedError

IpAddr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


def allow_private_network(explicit_flag: bool = False) -> bool:
    """Return True when private/loopback network access is explicitly opted in."""
    if explicit_flag:
        return True
    for env_key in ("OBSCURA_ALLOW_PRIVATE_NETWORK", "IRALENS_ALLOW_PRIVATE_URLS"):
        val = os.environ.get(env_key, "").strip().lower()
        if val in ("1", "true", "yes"):
            return True
    return False


def is_blocked_v4(v4: ipaddress.IPv4Address) -> bool:
    """True when an IPv4 address belongs to a range the SSRF policy blocks.

    Matches `obscura_ssrf::is_blocked_v4` (`crates/obscura-ssrf/src/lib.rs`):
    - Loopback (127.0.0.0/8)
    - Unspecified (0.0.0.0/8)
    - RFC 1918 private (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Link-local / cloud metadata (169.254.0.0/16)
    - Broadcast (255.255.255.255)
    - Multicast (224.0.0.0/4)
    - Carrier-grade NAT (100.64.0.0/10, RFC 6598)
    - IETF Protocol Assignments (192.0.0.0/24, RFC 6890)
    - Benchmarking (198.18.0.0/15, RFC 2544)
    """
    octets = v4.packed
    if (
        v4.is_loopback
        or v4.is_private
        or v4.is_link_local
        or v4.is_multicast
        or v4.is_unspecified
        or octets == b"\xff\xff\xff\xff"
    ):
        return True
    # 0.0.0.0/8 ("This network", RFC 1122)
    if octets[0] == 0:
        return True
    # 100.64.0.0/10 (Carrier-grade NAT, RFC 6598)
    if octets[0] == 100 and (octets[1] & 0xC0) == 64:
        return True
    # 192.0.0.0/24 (IETF Protocol Assignments, RFC 6890)
    if octets[0] == 192 and octets[1] == 0 and octets[2] == 0:
        return True
    # 198.18.0.0/15 (Benchmarking, RFC 2544)
    if octets[0] == 198 and (octets[1] & 0xFE) == 18:
        return True
    return False


def is_blocked_v6(v6: ipaddress.IPv6Address) -> bool:
    """True when an IPv6 address belongs to a range the SSRF policy blocks.

    Matches `obscura_ssrf::is_blocked_v6` (`crates/obscura-ssrf/src/lib.rs`).
    """
    if v6.is_loopback or v6.is_unspecified or v6.is_multicast:
        return True
    # Unique local (fc00::/7) and link-local unicast (fe80::/10)
    raw = v6.packed
    seg0 = (raw[0] << 8) | raw[1]
    seg1 = (raw[2] << 8) | raw[3]
    seg2 = (raw[4] << 8) | raw[5]
    seg3 = (raw[6] << 8) | raw[7]
    seg4 = (raw[8] << 8) | raw[9]
    seg5 = (raw[10] << 8) | raw[11]
    seg6 = (raw[12] << 8) | raw[13]
    seg7 = (raw[14] << 8) | raw[15]

    if (seg0 & 0xFE00) == 0xFC00 or (seg0 & 0xFFC0) == 0xFE80:
        return True

    # IPv4-mapped (::ffff:a.b.c.d)
    if v6.ipv4_mapped is not None:
        return is_blocked_v4(v6.ipv4_mapped)

    # IPv4-compatible (::a.b.c.d, deprecated RFC 4291 s2.5.5.1)
    if seg0 == 0 and seg1 == 0 and seg2 == 0 and seg3 == 0 and seg4 == 0 and seg5 == 0:
        embedded = ipaddress.IPv4Address(raw[12:16])
        return is_blocked_v4(embedded)

    # NAT64 well-known prefix (64:ff9b::/96, RFC 6052) and
    # local-use NAT64 prefix (64:ff9b:1::/48, RFC 8215)
    if seg0 == 0x0064 and seg1 == 0xFF9B:
        if (seg2 == 0 and seg3 == 0 and seg4 == 0 and seg5 == 0) or seg2 == 1:
            return True

    # 6to4 (2002::/16, RFC 3056) embeds IPv4 in segments 1 and 2
    if seg0 == 0x2002:
        embedded = ipaddress.IPv4Address(raw[2:6])
        if is_blocked_v4(embedded):
            return True

    # Teredo (2001:0000::/32, RFC 4380): client IPv4 is in segments 6..7 XOR 0xffffffff
    if seg0 == 0x2001 and seg1 == 0x0000:
        client_u32 = ((seg6 << 16) | seg7) ^ 0xFFFFFFFF
        client_v4 = ipaddress.IPv4Address(client_u32)
        if is_blocked_v4(client_v4):
            return True

    return False


def is_blocked_ip(ip: Union[str, IpAddr]) -> bool:
    """True when an IP address (v4 or v6) is blocked by the SSRF deny-set."""
    addr: IpAddr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    if isinstance(addr, ipaddress.IPv4Address):
        return is_blocked_v4(addr)
    return is_blocked_v6(addr)


def validate_navigation_url(
    url: str,
    *,
    allow_private: bool = False,
    allow_file: bool = False,
) -> Tuple[str, List[str]]:
    """Validate a navigation or subresource URL against scheme and SSRF policies.

    Returns `(normalized_url, resolved_ips)`.
    Raises `SecurityBlockedError` when blocked by scheme or SSRF policy,
    or `ValueError` for malformed URLs.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    cleaned = url.strip()
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise ValueError("URL contains control characters")

    parsed = urllib.parse.urlsplit(cleaned)
    scheme = (parsed.scheme or "").lower()

    if scheme == "file":
        if not allow_file:
            raise SecurityBlockedError(
                "file:// navigation is disabled for MCP",
                detail="file_scheme_disabled",
            )
        return cleaned, []

    if scheme in ("about",):
        if cleaned.lower() in ("about:blank", "about:srcdoc"):
            return cleaned, []
        raise SecurityBlockedError(
            f"Navigation to '{cleaned}' is blocked by security policy",
            detail="scheme_not_allowed",
        )

    if scheme not in ("http", "https"):
        raise SecurityBlockedError(
            f"URL scheme {scheme or '<missing>'!r} is not allowed (only http/https)",
            detail="scheme_not_allowed",
        )

    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no host: {cleaned!r}")

    private_ok = allow_private_network(allow_private)
    lowered_host = host.lower().rstrip(".")
    if lowered_host in ("metadata.google.internal", "instance-data", "metadata"):
        raise SecurityBlockedError(
            f"Refusing to connect to cloud metadata host {host!r} (SSRF protection)",
            detail="cloud_metadata_blocked",
        )
    if not private_ok and (
        lowered_host in ("localhost", "ip6-loopback", "ip6-localhost")
        or lowered_host.endswith((".localhost", ".local", ".internal"))
    ):
        raise SecurityBlockedError(
            f"Refusing to connect to local/internal host {host!r} (SSRF protection)",
            detail="private_host",
        )

    # Literal IP check
    try:
        literal_ip = ipaddress.ip_address(lowered_host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if literal_ip.is_link_local:
            raise SecurityBlockedError(
                f"Refusing to connect to link-local/metadata IP {literal_ip} (SSRF protection)",
                detail="link_local_blocked",
            )
        if not private_ok and is_blocked_ip(literal_ip):
            raise SecurityBlockedError(
                f"Refusing to connect to non-public IP {literal_ip} (SSRF protection)",
                detail="private_ip",
            )
        return cleaned, [str(literal_ip)]

    # DNS resolution check
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        # Leave DNS resolution errors to the transport layer so they classify as
        # network/page_unavailable errors rather than SSRF policy blocks.
        raise OSError(f"Network error: DNS resolution failed for {host}: {exc}") from exc

    resolved_ips: List[str] = []
    for _family, _socktype, _proto, _canon, sockaddr in infos:
        ip_str = str(sockaddr[0]).split("%", 1)[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if addr.is_link_local:
            raise SecurityBlockedError(
                f"Host {host!r} resolved to link-local/metadata address {addr} (SSRF protection)",
                detail="link_local_blocked",
            )
        if not private_ok and is_blocked_ip(addr):
            raise SecurityBlockedError(
                f"Host {host!r} resolved to non-public address {addr} (SSRF protection)",
                detail="dns_rebinding_blocked",
            )
        if str(addr) not in resolved_ips:
            resolved_ips.append(str(addr))

    return cleaned, resolved_ips
