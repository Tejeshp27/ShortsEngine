# =============================================================
# src/sources/public_web.py — safely download an article page
#
# Used when you paste an article link in the dashboard, or when a
# story's full text is fetched. A link typed into a web form could
# point at this computer or your home network (for example
# http://127.0.0.1/admin or http://192.168.1.1). Fetching such
# addresses from a server is a known attack ("SSRF"), so:
#
#   - only http/https on the normal ports (80, 443) are allowed
#   - every address the website's name resolves to must be public
#   - we connect to the exact IP address we checked, so a second
#     name lookup cannot quietly switch to a private address
#   - every redirect is checked the same way
#   - at most 2 MB of text is read
# =============================================================

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 2_000_000
TIMEOUT_SECONDS = 15
ALLOWED_PORTS = (80, 443)
TEXT_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
USER_AGENT = "ShortsEngine/0.1 (personal news-summary project)"


def check_url_shape(url, parts):
    """
    Refuse addresses that are not ordinary public web links.

    Args:
        url (str): The full address.
        parts: The address split into pieces by urlsplit().

    Raises:
        ValueError: For other schemes, logins in the address, line
                    breaks (which could inject extra headers) or very
                    long addresses.
    """
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Use a public http:// or https:// article URL without login details.")
    if parts.username or parts.password:
        raise ValueError("Use a public http:// or https:// article URL without login details.")
    for character in ("\r", "\n", "\x00"):
        if character in url:
            raise ValueError("The article URL is invalid or too long.")
    if len(url) > 2048:
        raise ValueError("The article URL is invalid or too long.")


def is_public_ip(address_text):
    """
    Check that an IP address belongs to the public internet.

    Args:
        address_text (str): For example "151.101.1.140".

    Returns:
        bool: False for this computer (127.x), home networks (192.168.x,
              10.x), link-local, multicast and reserved addresses.
    """
    address = ipaddress.ip_address(address_text)
    if not address.is_global:
        return False
    if address.is_multicast or address.is_reserved:
        return False
    return True


def public_address(url):
    """
    Check a link and look up the public IP address to connect to.

    Args:
        url (str): The article address.

    Returns:
        tuple: (the split-up address, the checked IP address, the port)

    Raises:
        ValueError: If the link is not allowed or the site can't be found.
    """
    parts = urlsplit(url)
    check_url_shape(url, parts)

    if parts.port:
        port = parts.port
    elif parts.scheme == "https":
        port = 443
    else:
        port = 80
    if port not in ALLOWED_PORTS:
        raise ValueError("Article URLs must use the standard web ports.")

    try:
        answers = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("The article website could not be found.") from error
    if not answers:
        raise ValueError("The article website could not be found.")

    # A name can resolve to several addresses. ALL must be public, or a
    # site could list one public and one private address.
    for answer in answers:
        ip_text = answer[4][0]
        if not is_public_ip(ip_text):
            raise ValueError("Only public article websites are allowed; local network addresses are blocked.")
    return parts, answers[0][4][0], port


class PublicHTTPSConnection(http.client.HTTPSConnection):
    """
    An HTTPS connection to an IP address we already checked.

    The normal HTTPSConnection would look the website's name up again,
    which could give a different (private) address. This version
    connects to the checked IP but still verifies the security
    certificate against the website's real name.

    (This "subclass" reuses everything from HTTPSConnection and only
    replaces its connect() step.)
    """

    def __init__(self, hostname, address, port):
        """
        Prepare the connection.

        Args:
            hostname (str): The website's name, e.g. "www.npr.org".
            address (str): The checked IP address to connect to.
            port (int): 443 for HTTPS.
        """
        super().__init__(hostname, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        self.checked_address = address

    def connect(self):
        """Open the connection to the checked IP and start encryption."""
        raw_socket = socket.create_connection((self.checked_address, self.port), timeout=TIMEOUT_SECONDS)
        try:
            # server_hostname makes the certificate check use the real name.
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def open_connection(parts, address, port):
    """
    Create a connection to a checked address.

    Args:
        parts: The split-up address.
        address (str): The checked IP.
        port (int): The port.

    Returns:
        An HTTP or HTTPS connection object (not yet sending anything).
    """
    if parts.scheme == "https":
        return PublicHTTPSConnection(parts.hostname, address, port)
    return http.client.HTTPConnection(address, port, timeout=TIMEOUT_SECONDS)


def request_path(parts):
    """
    Get the part of the address after the website name.

    Args:
        parts: The split-up address.

    Returns:
        str: For example "/2026/11/story?id=3".
    """
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return path


def read_text_page(response):
    """
    Read an article page's text from a successful response.

    Args:
        response: The HTTP response (status 200).

    Returns:
        str: The page's HTML or text.

    Raises:
        ValueError: If it isn't a text page or is larger than 2 MB.
    """
    content_type = response.getheader("Content-Type", "").lower()
    is_text = False
    for kind in TEXT_CONTENT_TYPES:
        if kind in content_type:
            is_text = True
    if not is_text:
        raise ValueError("The source must be an ordinary text article page.")

    # Read one byte more than allowed, to notice a page that is too big.
    data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("This article page is too large. Choose another source.")
    return data.decode("utf-8", errors="replace")


def download_public_html(url):
    """
    Download an article page, checking the address and every redirect.

    Args:
        url (str): The article address.

    Returns:
        str: Up to 2 MB of the page's HTML.

    Raises:
        ValueError: For blocked addresses, non-text pages, failed
                    requests or too many redirects.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts, address, port = public_address(current)
        connection = open_connection(parts, address, port)
        try:
            # "Host" tells the server which website we want, since we
            # connected by IP address. "identity" asks for no compression.
            connection.request("GET", request_path(parts), headers={
                "Host": parts.netloc,
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()

            if response.status in REDIRECT_STATUSES:
                target = response.getheader("Location")
                if not target:
                    raise ValueError("The article website returned an empty redirect.")
                # The next loop checks the new address before connecting.
                current = urljoin(current, target)
                continue
            if response.status != 200:
                raise ValueError(f"The article website returned HTTP {response.status}. Choose another source.")
            return read_text_page(response)
        except (OSError, http.client.HTTPException) as error:
            raise ValueError("Could not read the source article. Check the URL or choose another source.") from error
        finally:
            connection.close()
    raise ValueError("The article website redirected too many times.")
