"""Launch the dashboard with uvicorn, picking a free port if the default is busy."""

from __future__ import annotations

import contextlib
import socket
import webbrowser

from axiom_fabric_dashboard import DEFAULT_PORT


def find_open_port(host: str, start: int, attempts: int = 20) -> int:
    """Return the first bindable port at or after `start`."""
    for candidate in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}-{start + attempts - 1} on {host}.")


def run(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
) -> None:
    import uvicorn

    from axiom_fabric_dashboard.app import app

    chosen = find_open_port(host, port)
    if chosen != port:
        print(f"Port {port} is in use; using {chosen} instead.")

    url = f"http://{host}:{chosen}"
    print(f"Axiom Fabric dashboard → {url}  (Ctrl+C to stop)")
    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)

    uvicorn.run(app, host=host, port=chosen, log_level="info")
