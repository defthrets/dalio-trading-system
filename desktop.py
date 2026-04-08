"""
DALIOS Desktop Application
Launches the FastAPI server in a background thread and opens a native window.
"""

import multiprocessing
import os
import sys
import threading
import time
import socket

# Ensure the project root is on sys.path
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    BASE_DIR = os.path.dirname(sys.executable)
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


def _wait_for_server(port: int, timeout: float = 30.0):
    """Block until the server is accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


def main():
    port = 8000

    # If port is taken, try a few alternatives
    if not _port_free(port):
        for alt in [8001, 8080, 8888]:
            if _port_free(alt):
                port = alt
                break

    # Start server in background thread
    server_thread = threading.Thread(target=_start_server, args=(port,), daemon=True)
    server_thread.start()

    # Wait for server to be ready
    if not _wait_for_server(port):
        print("ERROR: Server failed to start within 30 seconds")
        sys.exit(1)

    # Open native window
    import webview
    window = webview.create_window(
        title="DALIOS — Automated Trading Framework",
        url=f"http://127.0.0.1:{port}",
        width=1400,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
