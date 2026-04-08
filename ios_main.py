"""
DALIOS iOS Application
Runs FastAPI/uvicorn on localhost and displays the UI in a Kivy WebView.
Same architecture as Android — Python runs natively on the device.
"""

import os
import sys
import threading
import time
import socket

# Set up paths
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


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


# --- Kivy App ---
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.clock import Clock

try:
    from pyobjus import autoclass
    WKWebView = autoclass('WKWebView')
    NSURL = autoclass('NSURL')
    NSURLRequest = autoclass('NSURLRequest')
    HAS_IOS_WEBVIEW = True
except ImportError:
    HAS_IOS_WEBVIEW = False


class DaliosApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.port = 8000
        self.server_ready = False

    def build(self):
        self.title = "DALIOS"

        if not _port_free(self.port):
            for alt in [8001, 8080, 8888]:
                if _port_free(alt):
                    self.port = alt
                    break

        # Start server in background thread
        server_thread = threading.Thread(
            target=_start_server, args=(self.port,), daemon=True
        )
        server_thread.start()

        # Show loading screen
        self.root = BoxLayout(orientation='vertical')
        self.status_label = Label(
            text="[color=00ff41]DALIOS[/color]\n\nInitialising trading systems...",
            markup=True,
            font_size='18sp',
            halign='center',
        )
        self.root.add_widget(self.status_label)

        Clock.schedule_interval(self._check_server, 0.5)
        return self.root

    def _check_server(self, dt):
        if not _port_free(self.port):
            self.server_ready = True
            Clock.unschedule(self._check_server)
            self._load_webview()

    def _load_webview(self):
        url = f"http://127.0.0.1:{self.port}"

        if HAS_IOS_WEBVIEW:
            # Use native iOS WKWebView via pyobjus
            from pyobjus import autoclass
            UIApplication = autoclass('UIApplication')
            app = UIApplication.sharedApplication()
            window = app.keyWindow
            root_vc = window.rootViewController

            webview = WKWebView.alloc().initWithFrame_(window.bounds)
            ns_url = NSURL.URLWithString_(url)
            request = NSURLRequest.requestWithURL_(ns_url)
            webview.loadRequest_(request)

            root_vc.view.addSubview_(webview)
        else:
            # Fallback: open in Safari
            import webbrowser
            webbrowser.open(url)
            self.status_label.text = (
                f"[color=00ff41]DALIOS[/color]\n\n"
                f"Server running on port {self.port}\n"
                f"Opened in Safari.\n\n"
                f"[color=888888]Keep this app open.[/color]"
            )


if __name__ == "__main__":
    DaliosApp().run()
