"""Minimal Redfish client built on requests."""

import sys
import time
from urllib.parse import urlsplit

import urllib3

from ..errors import AuthError, TransportError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import requests
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("nodetools requires the 'requests' package") from exc


class Redfish:
    """A basic, cookieless Redfish session for a single BMC."""

    def __init__(self, address, user, password, timeout=10, verify=False):
        self.address = address
        self.debug_label = None
        self.user = user
        self.timeout = timeout
        self.verify = verify
        self._deadline = None
        self._etags = {}
        self.transport_errors = 0
        self.timeout_reason = None
        self._session = requests.Session()
        self._session.verify = verify
        self._session.auth = (user, password) if user else None
        self._session.headers.update({"Accept": "application/json"})

    def _debug(self, action, path, detail):
        if self.debug_label:
            try:
                resource = urlsplit(path).path
            except ValueError:
                resource = "<invalid path>"
            print("[redfish:{}] {} {!r} {}".format(self.debug_label, action, resource, detail),
                  file=sys.stderr, flush=True)

    def trace_source(self, label, path, field):
        self._debug("source {!r} <-".format(label), path, "field=" + field)

    def set_deadline(self, deadline):
        """Absolute time.monotonic() value past which no new request is made."""
        self._deadline = deadline

    def _remaining(self):
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def _expired(self):
        remaining = self._remaining()
        return remaining is not None and remaining <= 0

    def _timeout(self, timeout=None):
        read = timeout or self.timeout
        remaining = self._remaining()
        if remaining is None:
            return (min(3.0, read), read)
        return urllib3.util.Timeout(total=max(1e-6, remaining), connect=min(3.0, read), read=read)

    def url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return "https://{}{}".format(self.address, path)

    def get(self, path, timeout=None):
        """Return parsed JSON, or None if the resource is missing/unreadable."""
        status, body = self.get_raw(path, timeout=timeout)
        if status is None:
            return None
        if isinstance(body, (dict, list)):
            return body
        return None

    def get_raw(self, path, timeout=None):
        """Return (status, parsed-or-text). status is None on transport error."""
        self.timeout_reason = None
        if self._expired():
            self._debug("GET", path, "deadline exceeded")
            self.timeout_reason = "node deadline exceeded before Redfish GET"
            return None, None
        try:
            resp = self._session.get(
                self.url(path), timeout=self._timeout(timeout)
            )
        except requests.RequestException as exc:
            self.transport_errors += 1
            self._debug("GET", path, type(exc).__name__)
            if isinstance(exc, requests.Timeout) or any(
                isinstance(arg, urllib3.exceptions.ReadTimeoutError) for arg in exc.args
            ):
                self.timeout_reason = "Redfish GET timed out"
            return None, None
        self._debug("GET", path, "HTTP {}".format(resp.status_code))
        url = self.url(path)
        etag = resp.headers.get("ETag") if resp.ok else None
        if etag:
            self._etags[url] = etag
        else:
            self._etags.pop(url, None)
        if resp.status_code in (401, 403):
            return resp.status_code, None
        text = resp.text
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, text

    def post(self, path, body, timeout=None):
        if self._expired():
            self._debug("POST", path, "deadline exceeded")
            raise TransportError("deadline exceeded before POST {}".format(path))
        try:
            resp = self._session.post(
                self.url(path), json=body, timeout=self._timeout(timeout)
            )
        except requests.RequestException as exc:
            self.transport_errors += 1
            self._debug("POST", path, type(exc).__name__)
            raise TransportError(str(exc)) from exc
        self._debug("POST", path, "HTTP {}".format(resp.status_code))
        if resp.status_code in (401, 403):
            raise AuthError("POST {} returned {}".format(path, resp.status_code))
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text

    def patch(self, path, body, timeout=None, etag_source=None):
        if self._expired():
            self._debug("PATCH", path, "deadline exceeded")
            raise TransportError("deadline exceeded before PATCH {}".format(path))
        try:
            url = self.url(path)
            source = self.url(etag_source) if etag_source else url
            etag = self._etags.pop(source, None)
            kwargs = {"headers": {"If-Match": etag}} if etag else {}
            resp = self._session.patch(
                url, json=body, timeout=self._timeout(timeout), **kwargs
            )
        except requests.RequestException as exc:
            self.transport_errors += 1
            self._debug("PATCH", path, type(exc).__name__)
            raise TransportError(str(exc)) from exc
        self._debug("PATCH", path, "HTTP {}".format(resp.status_code))
        if resp.status_code in (401, 403):
            raise AuthError("PATCH {} returned {}".format(path, resp.status_code))
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text

    def close(self):
        self._session.close()
