# =============================================================
# Download a user-supplied article without exposing local services.
# Each redirect is checked, and the connection uses the checked IP
# address so a second DNS lookup cannot redirect us to a private IP.
# Only ordinary public HTTP(S) article pages are accepted.
# =============================================================

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit


def public_address(url):
    """Validate url and return (parsed URL, public IP, port).

    Local addresses, credentials and unusual ports raise ValueError. All DNS
    answers must be public before any connection is made.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use a public http:// or https:// article URL without login details.")
    if any(character in url for character in ("\r", "\n", "\x00")) or len(url) > 2048:
        raise ValueError("The article URL is invalid or too long.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("Article URLs must use the standard web ports.")
    try:
        answers = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("The article website could not be found.") from error
    if not answers:
        raise ValueError("The article website could not be found.")
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError("Only public article websites are allowed; local network addresses are blocked.")
    return parts, answers[0][4][0], port


class PublicHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked IP while verifying the original website certificate.

    This small subclass is needed to avoid a second, potentially changed DNS
    lookup. HTTPS still checks the real hostname, not the numeric address.
    """

    def __init__(self, hostname, address, port):
        """Remember hostname, checked address and port for a 15-second connection."""
        super().__init__(hostname, port, timeout=15, context=ssl.create_default_context())
        self.checked_address = address

    def connect(self):
        """Open the checked address and verify its TLS certificate; return nothing."""
        raw_socket = socket.create_connection((self.checked_address, self.port), timeout=15)
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def download_public_html(url):
    """Return up to 2 MB of public article HTML from url, checking redirects.

    Raises ValueError for blocked addresses, non-text files or failed requests.
    """
    current = url
    for redirect_number in range(6):
        parts, address, port = public_address(current)
        if parts.scheme == "https":
            connection = PublicHTTPSConnection(parts.hostname, address, port)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=15)
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={"Host": parts.netloc,
                "User-Agent": "ShortsEngine/0.1 (personal news-summary project)", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader("Location")
                if not target:
                    raise ValueError("The article website returned an empty redirect.")
                current = urljoin(current, target)
                continue
            if response.status != 200:
                raise ValueError(f"The article website returned HTTP {response.status}. Choose another source.")
            content_type = response.getheader("Content-Type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                raise ValueError("The source must be an ordinary text article page.")
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("This article page is too large. Choose another source.")
            return data.decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException) as error:
            raise ValueError("Could not read the source article. Check the URL or choose another source.") from error
        finally:
            connection.close()
    raise ValueError("The article website redirected too many times.")
