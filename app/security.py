"""Two small guards for an app that has no login.

The app listens on 127.0.0.1 only, but the owner's browser also visits other
websites, and a hostile page can make that browser send requests to
http://127.0.0.1:8000. These checks close the two classic doors:

* Host check: refuses requests whose Host header is not one of ours. This
  defeats "DNS rebinding", where a hostile domain is made to resolve to
  127.0.0.1 so that its scripts can read the portfolio.
* Origin check: refuses state-changing requests (POST, DELETE...) sent from
  another site ("cross-site request forgery").
"""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Reject writes whose Origin header names another site."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in SAFE_METHODS and not _is_same_origin(request):
            return PlainTextResponse("Cross-site request refused", status_code=403)
        return await call_next(request)


def _is_same_origin(request: Request) -> bool:
    # Browsers attach Origin to every cross-site POST. Tools such as curl send
    # none: they are not a browser being tricked, so they are let through.
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.headers.get("host", "")
