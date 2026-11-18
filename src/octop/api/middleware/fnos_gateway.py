"""Serve Octop behind the fnOS unified gateway (fork).

The fnOS desktop opens apps either in an iframe on ``http://<host>:<port>`` or
through its unified gateway: the browser stays on the fnOS origin (http *or*
https, LAN or remote) and fnOS forwards ``/app/<appname>/…`` to a Unix socket
the app listens on, keeping the prefix in the path. A fixed-protocol iframe
goes blank when fnOS is opened over https (mixed content), so the native
package uses the gateway.

This module is only mounted on that Unix socket listener (see ``launch.py``);
the regular TCP port is untouched. It

* strips the prefix before the request reaches the app, and
* rewrites HTML pages so the dashboard works under the prefix: root-relative
  ``src``/``href`` attributes get the prefix, and a small bootstrap script sets
  ``window.__OCTOP_BASE__`` (read by ``dashboard/src/utils/publicUrl.ts``) and
  prefixes the root-relative / same-origin URLs the page requests at runtime
  (fetch, XHR, WebSocket, EventSource, ``window.open``, and ``src``/``href``
  attributes added later, e.g. media links in chat messages).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

GATEWAY_SOCKET_ENV = "OCTOP_GATEWAY_SOCKET"
GATEWAY_PREFIX_ENV = "OCTOP_GATEWAY_PREFIX"

_PREFIX_RE = re.compile(r"^(/[A-Za-z0-9._-]+)+$")
_ATTR_RE = re.compile(rb"""(\s(?:src|href)=["'])/(?!/)""")
_HEAD_RE = re.compile(rb"<head(\s[^>]*)?>", re.IGNORECASE)
# Responses that may stream or be huge are never buffered for rewriting.
_MAX_REWRITE_BYTES = 4 * 1024 * 1024

_BOOTSTRAP_JS = r"""(function(){
var B=%(prefix)s;window.__OCTOP_BASE__=B+"/";
function under(p){return p===B||p.indexOf(B+"/")===0;}
function fix(u){
if(u instanceof URL)u=u.href;
if(typeof u!=="string")return u;
if(u.charAt(0)==="/"&&u.charAt(1)!=="/")return under(u)?u:B+u;
if(!/^(https?|wss?):\/\//i.test(u))return u;
try{var x=new URL(u);if(x.host===location.host&&!under(x.pathname)){x.pathname=B+x.pathname;return x.href;}}catch(e){}
return u;}
window.__octopGatewayUrl=fix;
var f=window.fetch;if(f)window.fetch=function(i,o){
if(typeof Request!=="undefined"&&i instanceof Request){var n=fix(i.url);if(n!==i.url)i=new Request(n,i);}else i=fix(i);
return f.call(this,i,o);};
var xo=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){
var a=Array.prototype.slice.call(arguments);a[1]=fix(u);return xo.apply(this,a);};
function wrap(name){var C=window[name];if(!C)return;
var W=function(u,p){return arguments.length>1?new C(fix(u),p):new C(fix(u));};
W.prototype=C.prototype;for(var k in C)try{W[k]=C[k];}catch(e){}
["CONNECTING","OPEN","CLOSING","CLOSED"].forEach(function(k){if(k in C)try{Object.defineProperty(W,k,{value:C[k]});}catch(e){}});
window[name]=W;}
wrap("WebSocket");wrap("EventSource");
var wo=window.open;window.open=function(u){var a=Array.prototype.slice.call(arguments);if(a.length)a[0]=fix(u);return wo.apply(window,a);};
if(navigator.sendBeacon){var sb=navigator.sendBeacon.bind(navigator);navigator.sendBeacon=function(u,d){return sb(fix(u),d);};}
var ATTRS=["src","href","poster","action","data"];
var sa=Element.prototype.setAttribute;Element.prototype.setAttribute=function(n,v){
if(typeof v==="string"&&ATTRS.indexOf(String(n).toLowerCase())!==-1)v=fix(v);return sa.call(this,n,v);};
[["HTMLImageElement","src"],["HTMLScriptElement","src"],["HTMLIFrameElement","src"],["HTMLMediaElement","src"],
["HTMLSourceElement","src"],["HTMLVideoElement","poster"],["HTMLAnchorElement","href"],["HTMLLinkElement","href"],
["HTMLFormElement","action"]].forEach(function(p){var C=window[p[0]];if(!C)return;
var d=Object.getOwnPropertyDescriptor(C.prototype,p[1]);if(!d||!d.set||!d.configurable)return;
Object.defineProperty(C.prototype,p[1],{configurable:true,enumerable:d.enumerable,get:d.get,
set:function(v){d.set.call(this,typeof v==="string"?fix(v):v);}});});
function fixEl(el){if(!el||el.nodeType!==1)return;
for(var i=0;i<ATTRS.length;i++){var v=el.getAttribute(ATTRS[i]);if(v){var n=fix(v);if(n!==v)el.setAttribute(ATTRS[i],n);}}}
function fixTree(n){if(!n||n.nodeType!==1)return;fixEl(n);
if(n.querySelectorAll){var l=n.querySelectorAll("[src],[href],[poster],[action],[data]");for(var i=0;i<l.length;i++)fixEl(l[i]);}}
new MutationObserver(function(ms){ms.forEach(function(m){
if(m.type==="attributes")fixEl(m.target);else for(var i=0;i<m.addedNodes.length;i++)fixTree(m.addedNodes[i]);});})
.observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:ATTRS});
})();"""


def gateway_listener_from_env() -> tuple[str, str] | None:
    """Return ``(socket_path, prefix)`` when the gateway listener is configured."""
    if os.name != "posix":
        return None
    socket_path = os.environ.get(GATEWAY_SOCKET_ENV, "").strip()
    prefix = normalize_prefix(os.environ.get(GATEWAY_PREFIX_ENV, ""))
    if not socket_path or prefix is None:
        return None
    return socket_path, prefix


def normalize_prefix(raw: str) -> str | None:
    """``/app/octop-native/`` → ``/app/octop-native``; ``None`` when unusable."""
    prefix = "/" + raw.strip().strip("/")
    if prefix == "/" or not _PREFIX_RE.match(prefix):
        return None
    return prefix


def bootstrap_script(prefix: str) -> bytes:
    return (_BOOTSTRAP_JS % {"prefix": json.dumps(prefix)}).encode()


def rewrite_html(body: bytes, prefix: str) -> bytes:
    """Prefix root-relative attributes and inject the bootstrap script."""
    out = _ATTR_RE.sub(lambda m: m.group(1) + prefix.encode() + b"/", body)
    tag = b"<script>" + bootstrap_script(prefix) + b"</script>"
    match = _HEAD_RE.search(out)
    if match is None:
        return tag + out
    return out[: match.end()] + tag + out[match.end() :]


class GatewayPrefixMiddleware:
    """ASGI wrapper for requests arriving through the fnOS gateway socket."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if scope["type"] == "http" and path == self.prefix:
            query = scope.get("query_string", b"")
            location = self.prefix + "/" + ("?" + query.decode("latin-1") if query else "")
            await send(
                {
                    "type": "http.response.start",
                    "status": 307,
                    "headers": [(b"location", location.encode("latin-1"))],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        if not path.startswith(self.prefix + "/"):
            await self.app(scope, receive, send)
            return

        scope = dict(scope)
        scope["path"] = path[len(self.prefix) :]
        raw_path = scope.get("raw_path")
        if isinstance(raw_path, bytes) and raw_path.startswith(self.prefix.encode() + b"/"):
            scope["raw_path"] = raw_path[len(self.prefix) :]
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return
        # Force plain body messages so HTML can be rewritten.
        extensions = dict(scope.get("extensions") or {})
        extensions.pop("http.response.pathsend", None)
        extensions.pop("http.response.zerocopysend", None)
        scope["extensions"] = extensions
        await self.app(scope, receive, _HtmlRewriter(send, self.prefix).send)


class _HtmlRewriter:
    def __init__(self, send: Send, prefix: str) -> None:
        self._send = send
        self._prefix = prefix
        self._start: Message | None = None
        self._chunks: list[bytes] = []
        self._size = 0
        self._passthrough = False

    async def send(self, message: Message) -> None:
        kind = message["type"]
        if kind == "http.response.start":
            if self._should_rewrite(message):
                self._start = message
            else:
                self._passthrough = True
                await self._send(message)
            return
        if kind != "http.response.body" or self._passthrough or self._start is None:
            await self._send(message)
            return
        body = message.get("body", b"")
        self._chunks.append(body)
        self._size += len(body)
        if self._size > _MAX_REWRITE_BYTES:
            await self._flush(rewrite=False, more_body=message.get("more_body", False))
            return
        if message.get("more_body", False):
            return
        await self._flush(rewrite=True, more_body=False)

    def _should_rewrite(self, message: Message) -> bool:
        headers = {k.lower(): v for k, v in message.get("headers", [])}
        content_type = headers.get(b"content-type", b"").lower()
        return content_type.startswith(b"text/html") and b"content-encoding" not in headers

    async def _flush(self, *, rewrite: bool, more_body: bool) -> None:
        assert self._start is not None
        body = b"".join(self._chunks)
        headers = list(self._start.get("headers", []))
        if rewrite:
            body = rewrite_html(body, self._prefix)
            headers = [
                (k, v)
                for k, v in headers
                if k.lower() not in (b"content-length", b"etag", b"last-modified")
            ]
            headers.append((b"content-length", str(len(body)).encode()))
        start = dict(self._start)
        start["headers"] = headers
        self._start = None
        self._passthrough = True
        await self._send(start)
        await self._send({"type": "http.response.body", "body": body, "more_body": more_body})
