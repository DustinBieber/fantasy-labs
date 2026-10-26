"""Single place outbound HTTP clients are made, so tests can swap in an httpx.MockTransport."""
import httpx

transport: httpx.BaseTransport | None = None


def client(timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)
