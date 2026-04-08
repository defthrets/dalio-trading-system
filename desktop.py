"""
DALIOS Desktop Application
Launches the FastAPI server in a background thread and opens a native window.
Shows a military/hacker-style boot splash while the server initialises.
"""

import multiprocessing
import os
import sys
import threading
import time
import socket

# Ensure the project root is on sys.path
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.join(os.path.dirname(sys.executable), '_internal')
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def _start_server(port: int):
    """Run uvicorn in the current thread (blocking)."""
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


def _wait_for_server(port: int, timeout: float = 60.0):
    """Block until the server is accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


class Api:
    """Exposed to JS in the splash screen."""
    def __init__(self, window, port):
        self._window = window
        self._port = port

    def boot_complete(self):
        """Called by splash JS after the boot animation finishes."""
        if _wait_for_server(self._port, timeout=30):
            self._window.load_url(f"http://127.0.0.1:{self._port}")
        else:
            self._window.evaluate_js(
                "document.getElementById('ptxt').textContent='SERVER ERROR — RESTART APP';"
                "document.getElementById('ptxt').style.color='#ff3333';"
            )


def main():
    port = 8000

    if not _port_free(port):
        for alt in [8001, 8080, 8888]:
            if _port_free(alt):
                port = alt
                break

    # Start server in background thread
    server_thread = threading.Thread(target=_start_server, args=(port,), daemon=True)
    server_thread.start()

    import webview

    # Load splash HTML from file
    splash_path = os.path.join(BASE_DIR, 'ui', 'splash.html')
    splash_url = 'file:///' + splash_path.replace('\\', '/')

    window = webview.create_window(
        title="DALIOS — Automated Trading Framework",
        url=splash_url,
        width=1400,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        text_select=True,
    )

    api = Api(window, port)
    window.expose(api.boot_complete)

    webview.start(debug=False, private_mode=False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
