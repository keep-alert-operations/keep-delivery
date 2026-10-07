"""Test-only response timing around the unchanged production ASGI application."""

from time import perf_counter

TIMING_HEADER = b"x-appchi-diagnostic-asgi-ms"


class ResponseTiming:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = perf_counter()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != TIMING_HEADER
                ]
                headers.append((TIMING_HEADER, f"{(perf_counter() - started) * 1000:.3f}".encode()))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, timed_send)


def create_app():
    # Preserve the production app, middleware, authorization, budgets and lifespan.
    # It does not inspect request headers/bodies or log response contents.
    from appchi.main import app

    return ResponseTiming(app)
