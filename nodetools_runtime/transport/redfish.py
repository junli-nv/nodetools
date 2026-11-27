"""Minimal Redfish client built on requests."""

import json
import shlex
import ssl
import sys
import time
from urllib.parse import unquote, urlsplit

import urllib3

from ..config import DEFAULT_REDFISH_CONNECT_TIMEOUT, DEFAULT_REDFISH_TIMEOUT
from ..diagnostics import debug
from ..errors import AuthError, TransportError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import requests
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("nodetools requires the 'requests' package") from exc


_UNVERIFIED_CONTEXT = urllib3.util.ssl_.create_urllib3_context(cert_reqs=ssl.CERT_NONE)


class _UnverifiedAdapter(requests.adapters.HTTPAdapter):
    """Reuse TLS setup only for the existing certificate-unverified mode."""

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs["ssl_context"] = _UNVERIFIED_CONTEXT
        return super().init_poolmanager(connections, maxsize, block=block, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs["ssl_context"] = _UNVERIFIED_CONTEXT
        return super().proxy_manager_for(proxy, **kwargs)


class Redfish:
    """A basic, cookieless Redfish session for a single BMC."""

    def __init__(self, address, user, password, timeout=DEFAULT_REDFISH_TIMEOUT,
                 verify=False, connect_timeout=DEFAULT_REDFISH_CONNECT_TIMEOUT):
        self.address = address
        self.debug_label = None
        self.user = user
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.verify = verify
        self._deadline = None
        self._etags = {}
        self.transport_errors = 0
        self.read_error = None
        self.timeout_reason = None
        self._session = requests.Session()
        if verify is False and hasattr(self._session, "mount"):
            self._session.mount("https://", _UnverifiedAdapter())
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

    def _debug_request(self, method, url, body=None, headers=None, follow_redirects=False):
        if not self.debug_label:
            return
        parsed = urlsplit(url)
        auth = self._session.auth
        if not auth and parsed.username is not None:
            auth = (unquote(parsed.username), unquote(parsed.password or ""))
        url = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]).geturl()
        argv = ["curl"]
        if self._session.verify is False:
            argv.append("-k")
        argv.append("-s")
        if auth:
            user, password = auth
            argv.extend(["--user", "{}:{}".format(user, password or "")])
        argv.append("--globoff")
        if follow_redirects:
            argv.append("-L")
        if method != "GET":
            argv.extend(["-X", method])
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        for key, value in request_headers.items():
            argv.extend(["-H", "{}: {}".format(key, value)])
        if body is not None:
            argv.extend(["--data-raw", json.dumps(body, allow_nan=False)])
        argv.append(url)
        debug(True, "redfish:" + self.debug_label, shlex.join(argv))

    def _debug_error(self, path, status, body):
        if not self.debug_label or status < 400 or not isinstance(body, dict):
            return
        error = body.get("error") or body
        if not isinstance(error, dict):
            return
        messages = error.get("@Message.ExtendedInfo") or [error]
        for item in messages:
            if isinstance(item, dict):
                message = {k: item[k] for k in ("MessageId", "Message", "Resolution", "code", "message") if k in item}
                if message:
                    self._debug("error", path, json.dumps(message, ensure_ascii=False))

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
        connect = min(self.connect_timeout, read)
        if remaining is None:
            return (connect, read)
        return urllib3.util.Timeout(total=max(1e-6, remaining), connect=connect, read=read)

    def url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return "https://{}{}".format(self.address, path)

    def get(self, path, timeout=None):
        """Return parsed JSON, or None if the resource is missing/unreadable."""
        if self.read_error is not None:
            raise self.read_error
        status, body = self.get_raw(path, timeout=timeout)
        if status == 401:
            self.read_error = AuthError("Redfish GET {} returned HTTP 401".format(path))
        if status is None and self._expired():
            self.read_error = TransportError("node deadline exceeded during Redfish GET")
            self.read_error.abort_node = True
            return None
        if self.read_error is not None:
            self.read_error.abort_node = True
            raise self.read_error
        if status is None:
            return None
        if isinstance(body, (dict, list)):
            return body
        return None

    def get_raw(self, path, timeout=None, *, follow_redirects=True):
        """Return (status, parsed-or-text). status is None on transport error."""
        self.timeout_reason = None
        if self._expired():
            self._debug("GET", path, "deadline exceeded")
            self.timeout_reason = "node deadline exceeded before Redfish GET"
            return None, None
        url = self.url(path)
        self._debug_request("GET", url, follow_redirects=follow_redirects)
        try:
            flags = {} if follow_redirects else {"allow_redirects": False}
            resp = self._session.get(url, timeout=self._timeout(timeout), **flags)
        except requests.RequestException as exc:
            self.transport_errors += 1
            if isinstance(exc, (requests.ConnectionError, requests.ConnectTimeout)):
                self.read_error = TransportError("BMC connection failed: " + type(exc).__name__)
                self.read_error.abort_node = True
            self._debug("GET", path, type(exc).__name__)
            if isinstance(exc, requests.Timeout) or any(
                isinstance(arg, urllib3.exceptions.ReadTimeoutError) for arg in exc.args
            ):
                self.timeout_reason = "Redfish GET timed out"
            return None, None
        self._debug("GET", path, "HTTP {}".format(resp.status_code))
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        self._debug_error(path, resp.status_code, body)
        etag = resp.headers.get("ETag")
        if not etag and isinstance(body, dict) and isinstance(body.get("@odata.etag"), str):
            etag = body["@odata.etag"]
        if resp.ok and etag:
            self._etags[url] = etag
        else:
            self._etags.pop(url, None)
        if resp.status_code in (401, 403):
            error = (body.get("error") or body) if isinstance(body, dict) else {}
            messages = error.get("@Message.ExtendedInfo") or [error]
            if any(isinstance(item, dict) and
                   str(item.get("MessageId", "")).endswith(".PasswordChangeRequired")
                   for item in messages):
                self.read_error = AuthError("BMC account requires a password change")
                self.read_error.abort_node = True
            return resp.status_code, None
        return resp.status_code, body

    def post(self, path, body, timeout=None):
        if self._expired():
            self._debug("POST", path, "deadline exceeded")
            raise TransportError("deadline exceeded before POST {}".format(path))
        url = self.url(path)
        self._debug_request("POST", url, body)
        try:
            resp = self._session.post(url, json=body, timeout=self._timeout(timeout))
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
            self._debug_request("PATCH", url, body, kwargs.get("headers"))
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
