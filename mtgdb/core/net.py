"""Shared low-level HTTP helpers for trusted upstream retrieval.

Requests to api.scryfall.com are paced and all requests carry an identifying
User-Agent and Accept header. Static Scryfall files and the official Wizards
Rules page/TXT keep those headers without inheriting Scryfall API pacing.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "MTGDeckBuilder/1.0"
# Wizards currently fronts magic.wizards.com with browser-oriented CDN/WAF
# behavior. Use a browser-compatible UA for those ordinary document requests
# while keeping the identifying application UA for Scryfall API traffic.
WIZARDS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
SCRYFALL_ACCEPT = "application/json;q=0.9,*/*;q=0.8"
WIZARDS_PAGE_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
WIZARDS_TEXT_ACCEPT = "text/plain;q=0.9,*/*;q=0.8"
_WIZARDS_HOSTS = {"magic.wizards.com", "www.magic.wizards.com", "media.wizards.com"}

ALLOWED_SCHEMES = ("https",)

_API_HOST = "api.scryfall.com"
_MIN_API_INTERVAL = 0.1  # seconds
_last_api_call = 0.0
_api_lock = threading.Lock()


def _is_api_url(url):
    """Return True only for requests sent to Scryfall's API hostname."""
    try:
        return urllib.parse.urlsplit(url).hostname == _API_HOST
    except (AttributeError, TypeError, ValueError):
        # urlsplit raises AttributeError for a non-string that is not None,
        # such as a Path a caller forgot to str(). This guard exists so no
        # caller has to pre-validate a URL, so it must be total.
        return False


def _verify_scheme(url):
    """Reject any URL this application is not meant to retrieve.

    urlopen is installed with handlers for file, ftp and data as well as
    http/https, so an unchecked URL is not merely a bad download: on Windows
    a "file://host/share/x" URL is an SMB connection to that host, which
    offers the machine's credentials to whoever answers. Every URL this
    application fetches comes from a Scryfall response or the card database,
    so all of them are https already; requiring it here means a hostile or
    corrupted upstream value fails loudly instead of reaching a handler that
    was never intended to be part of this transport.

    Python's redirect handler also permits ftp, so a legitimate https URL can
    still be redirected onto it; that redirect ends in this same check because
    the handler re-enters the opener.
    """
    try:
        # urlsplit normalizes the scheme to lower case already.
        scheme = urllib.parse.urlsplit(url).scheme
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"Unsupported download URL: {url!r}") from None
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"Refusing to retrieve a {scheme or 'scheme-less'} URL; "
            f"this application retrieves {'/'.join(ALLOWED_SCHEMES)} only")


def _throttle_api(url):
    """Serialize and pace Scryfall API calls; static file origins skip this."""
    if not _is_api_url(url):
        return

    global _last_api_call
    with _api_lock:
        wait = _MIN_API_INTERVAL - (time.monotonic() - _last_api_call)
        if wait > 0:
            time.sleep(wait)
        _last_api_call = time.monotonic()


def _request(url):
    """Build one host-appropriate request without changing source authority.

    Scryfall's API prefers JSON. Wizards' Rules page and media downloads are
    ordinary HTML/text resources; advertising JSON as the preferred response
    can produce a non-document representation on some CDN paths. Wizards is
    also browser-fronted, so those document requests use a browser-compatible
    User-Agent while Scryfall retains the identifying application User-Agent.
    """
    req = urllib.request.Request(url)
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold()
    except (AttributeError, TypeError, ValueError):
        host, path = "", ""
    if host in _WIZARDS_HOSTS:
        req.add_header("User-Agent", WIZARDS_USER_AGENT)
        accept = WIZARDS_TEXT_ACCEPT if path.endswith(".txt") else WIZARDS_PAGE_ACCEPT
        req.add_header("Accept-Language", "en-US,en;q=0.8")
        if host == "media.wizards.com":
            req.add_header("Referer", "https://magic.wizards.com/en/rules")
    else:
        req.add_header("User-Agent", USER_AGENT)
        accept = SCRYFALL_ACCEPT
    req.add_header("Accept", accept)
    return req


def _open(url, timeout):
    """Open a supported URL after applying API-only pacing when appropriate."""
    _verify_scheme(url)
    _throttle_api(url)
    return urllib.request.urlopen(_request(url), timeout=timeout)


def _declared_length(response):
    """Return a usable ``Content-Length`` or ``None`` when absent/unusable.

    A malformed header is treated as "not supplied" rather than raising, so a
    broken upstream header can never crash a transfer outside the bounded
    transient-retry policy. A declared length also cannot be compared against
    decoded bytes when the body arrives content-encoded, so an encoded response
    is treated as not supplying a usable length rather than as a short read.
    """
    try:
        headers = response.headers
    except AttributeError:
        return None
    try:
        encoding = (headers.get("Content-Encoding") or "").strip().casefold()
    except AttributeError:
        encoding = ""
    if encoding and encoding != "identity":
        return None
    try:
        raw = headers.get("Content-Length")
    except AttributeError:
        return None
    if not raw:
        return None
    try:
        declared = int(raw)
    except (TypeError, ValueError):
        return None
    return declared if declared >= 0 else None


def get_json(url, retries=2):
    """GET and parse JSON, retrying only transient transport/server failures."""
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            with _open(url, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not "power through" client/rate-limit errors such as 429.
            if exc.code < 500 or attempt >= attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt >= attempts - 1:
                raise
        time.sleep(0.6 * (2 ** attempt))


def fetch_bytes(url, retries=2):
    """GET raw bytes with the same bounded transient retry policy as downloads."""
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            with _open(url, timeout=30) as resp:
                declared = _declared_length(resp)
                data = resp.read()
                if declared is not None and len(data) != declared:
                    # Treated as transient so the shared bounded retry policy
                    # applies, exactly as it does for streamed bulk downloads.
                    raise OSError(
                        f"Incomplete HTTP response: expected {declared} bytes, "
                        f"received {len(data)}")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt >= attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt >= attempts - 1:
                raise
        time.sleep(0.6 * (2 ** attempt))
    raise RuntimeError("unreachable")


def _discard_partial(dest, opened):
    """Remove a partial download, but only one this attempt actually began."""
    if not opened:
        return
    try:
        os.remove(dest)
    except OSError:
        pass


def download(url, dest, progress_cb=None, retries=2):
    """
    Stream a URL to disk with bounded transient retries.

    Progress is intentionally throttled: a large Scryfall bulk file can contain
    thousands of 64-KB chunks, and forwarding every chunk to a GUI creates an
    unnecessary event storm. The callback is still guaranteed at the start,
    periodically during transfer, and once with the final byte count.
    """
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        # Cleanup removes a partial file this attempt wrote. It must not
        # remove a file that was already at the destination when a request
        # failed before any transfer began: deleting something this call never
        # opened turns a failed refresh into data loss.
        opened = False
        try:
            with _open(url, timeout=120) as resp:
                total = _declared_length(resp)
                read = 0
                chunk_size = 1 << 18  # 256 KB: efficient without large memory spikes
                last_report_time = 0.0
                last_report_bytes = 0
                if progress_cb:
                    progress_cb(0, total)
                with open(dest, "wb") as f:
                    # Set only once the destination is genuinely this call's
                    # to delete; a failing open() may be a file that was
                    # already there and could not be replaced.
                    opened = True
                    while True:
                        buf = resp.read(chunk_size)
                        if not buf:
                            break
                        f.write(buf)
                        read += len(buf)
                        now = time.monotonic()
                        if (progress_cb and
                                (read - last_report_bytes >= (1 << 20) or
                                 now - last_report_time >= 0.12)):
                            progress_cb(read, total)
                            last_report_time = now
                            last_report_bytes = read
                if total is not None and read != total:
                    raise OSError(
                        f"Incomplete HTTP download: expected {total} bytes, "
                        f"received {read}")
                if progress_cb and read != last_report_bytes:
                    progress_cb(read, total)
                return dest
        except urllib.error.HTTPError as exc:
            _discard_partial(dest, opened)
            if exc.code < 500 or attempt >= attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            _discard_partial(dest, opened)
            if attempt >= attempts - 1:
                raise
        time.sleep(0.8 * (2 ** attempt))
    return dest
