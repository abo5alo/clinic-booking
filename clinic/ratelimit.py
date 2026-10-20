import logging
from functools import wraps

from django.core.cache import cache
from django.shortcuts import render
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger(__name__)


def client_ip(request):
    # DRF's helper, so the web pages and the API agree on who the client is (honours NUM_PROXIES).
    return BaseThrottle().get_ident(request)


def ratelimit(scope, limit, period=60):
    """Allow at most `limit` POSTs per client IP per `period` seconds for a view; reply 429 after that."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method == "POST":
                key = f"ratelimit:{scope}:{client_ip(request)}"
                cache.add(key, 0, period)  # Fixed window: the counter starts when the first request arrives.
                try:
                    count = cache.incr(key)
                except ValueError:  # The key expired between add() and incr().
                    cache.set(key, 1, period)
                    count = 1
                if count > limit:
                    logger.warning("rate limit hit: scope=%s ip=%s", scope, client_ip(request))
                    return render(request, "429.html", status=429)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
