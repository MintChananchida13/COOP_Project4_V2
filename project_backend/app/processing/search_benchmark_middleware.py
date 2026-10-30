"""Record opt-in post-search ASGI intervals in the backend clock domain."""
import time
from urllib.parse import parse_qs

from app.processing.search_benchmark import record_span


class SearchBenchmarkMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        query = parse_qs(scope.get("query_string", b"").decode("ascii", errors="ignore"))
        trace = (query.get("benchmark_trace_id") or [""])[0]
        eligible = (path == "/api/layout/analyze" or path.startswith("/debug/detection-queries/") or
                    (path.startswith("/admin/templates/") and path.count("/") == 3))
        if not eligible or len(trace) != 32 or any(c not in "0123456789abcdef" for c in trace):
            return await self.app(scope, receive, send)
        started = time.perf_counter() if scope.get("method") in ("GET", "HEAD") else None
        finished = False

        async def measured_receive():
            nonlocal started
            message = await receive()
            if started is None and message.get("type") == "http.request" and not message.get("more_body", False):
                started = time.perf_counter()
            return message

        async def measured_send(message):
            nonlocal finished
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                finished = True

        try:
            await self.app(scope, measured_receive, measured_send)
        finally:
            if started is not None and finished:
                record_span(trace, "post", started, time.perf_counter())
