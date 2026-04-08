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
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


SPLASH_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: #0a0a0a;
  color: #00ff41;
  font-family: 'Courier New', monospace;
  overflow: hidden;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  position: relative;
}
/* CRT scanlines */
body::before {
  content: '';
  position: fixed;
  top:0;left:0;right:0;bottom:0;
  background: repeating-linear-gradient(
    0deg,
    transparent 0px,
    rgba(0,255,65,0.015) 1px,
    transparent 2px
  );
  pointer-events: none;
  z-index: 100;
}
body::after {
  content: '';
  position: fixed;
  top:0;left:0;right:0;bottom:0;
  background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.6) 100%);
  pointer-events: none;
  z-index: 99;
}

.boot-header {
  text-align: center;
  margin-bottom: 40px;
}
.boot-logo {
  font-size: 42px;
  font-weight: bold;
  color: #00ff41;
  text-shadow: 0 0 10px #00ff41, 0 0 30px rgba(0,255,65,0.3);
  letter-spacing: 12px;
  animation: logoFlicker 4s ease-in-out infinite;
}
.boot-sub {
  font-size: 11px;
  color: #00aa2a;
  letter-spacing: 6px;
  margin-top: 8px;
  opacity: 0.8;
}
.boot-classification {
  font-size: 10px;
  color: #ff3333;
  letter-spacing: 4px;
  margin-top: 14px;
  padding: 4px 16px;
  border: 1px solid #ff3333;
  display: inline-block;
  animation: blink 1.2s step-end infinite;
}

@keyframes logoFlicker {
  0%,100% { opacity:1; }
  92% { opacity:1; }
  93% { opacity:0.3; }
  94% { opacity:1; }
  96% { opacity:0.6; }
  97% { opacity:1; }
}
@keyframes blink {
  0%,100% { opacity:1; }
  50% { opacity:0; }
}

.terminal {
  width: 620px;
  background: rgba(0,20,0,0.4);
  border: 1px solid #00ff41;
  border-radius: 2px;
  padding: 20px;
  position: relative;
  box-shadow: 0 0 15px rgba(0,255,65,0.1), inset 0 0 30px rgba(0,0,0,0.5);
}
.terminal-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #004d00;
  padding-bottom: 8px;
  margin-bottom: 12px;
  font-size: 10px;
  color: #007700;
}

#log {
  font-size: 12px;
  line-height: 1.7;
  min-height: 240px;
  max-height: 240px;
  overflow: hidden;
}
#log .line {
  opacity: 0;
  animation: lineIn 0.1s forwards;
  white-space: nowrap;
}
@keyframes lineIn {
  to { opacity: 1; }
}
.line .ok { color: #00ff41; }
.line .warn { color: #ffaa00; }
.line .info { color: #0088ff; }
.line .dim { color: #005500; }
.line .bright { color: #00ff41; text-shadow: 0 0 6px #00ff41; }

.progress-wrap {
  margin-top: 16px;
  border: 1px solid #004d00;
  height: 18px;
  position: relative;
  background: #000;
}
.progress-bar {
  height: 100%;
  width: 0%;
  background: linear-gradient(90deg, #003300, #00ff41);
  box-shadow: 0 0 10px #00ff41;
  transition: width 0.3s ease;
}
.progress-text {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  color: #00ff41;
  letter-spacing: 2px;
  z-index: 2;
}

.status-row {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  font-size: 10px;
  color: #005500;
}
</style>
</head>
<body>

<div class="boot-header">
  <div class="boot-logo">DALIOS</div>
  <div class="boot-sub">AUTOMATED TRADING FRAMEWORK</div>
  <div class="boot-classification">▲ TOP SECRET // TRADING SYSTEMS</div>
</div>

<div class="terminal">
  <div class="terminal-header">
    <span>DALIOS BOOT SEQUENCE v3.7.1</span>
    <span id="clock">00:00:00</span>
  </div>
  <div id="log"></div>
  <div class="progress-wrap">
    <div class="progress-bar" id="pbar"></div>
    <div class="progress-text" id="ptxt">INITIALISING...</div>
  </div>
  <div class="status-row">
    <span id="sLeft">SUBSYSTEMS: STANDBY</span>
    <span id="sRight">ENCRYPTION: AES-256-GCM</span>
  </div>
</div>

<script>
const log = document.getElementById('log');
const pbar = document.getElementById('pbar');
const ptxt = document.getElementById('ptxt');
const sLeft = document.getElementById('sLeft');

// Clock
setInterval(() => {
  const d = new Date();
  document.getElementById('clock').textContent =
    d.toTimeString().split(' ')[0];
}, 1000);

const lines = [
  { t:200,  txt:'> BIOS POST CHECK .......................... <span class="ok">[PASS]</span>' },
  { t:400,  txt:'> MEMORY ALLOCATION 4096MB ................. <span class="ok">[OK]</span>' },
  { t:600,  txt:'> SECURE BOOT VERIFICATION ................. <span class="ok">[VERIFIED]</span>' },
  { t:900,  txt:'> LOADING KERNEL MODULES ................... <span class="ok">[OK]</span>' },
  { t:1100, txt:'<span class="dim">  ├── crypto.aes256 ..................... loaded</span>' },
  { t:1250, txt:'<span class="dim">  ├── net.tls1.3 ....................... loaded</span>' },
  { t:1400, txt:'<span class="dim">  └── sys.watchdog ..................... loaded</span>' },
  { t:1700, txt:'> INITIALISING TRADING ENGINE .............. <span class="info">[INIT]</span>' },
  { t:2000, txt:'> MOUNTING ENCRYPTED FILESYSTEM ............ <span class="ok">[MOUNTED]</span>' },
  { t:2300, txt:'> LOADING MARKET DATA FEEDS ................ <span class="ok">[CONNECTED]</span>' },
  { t:2600, txt:'> PORTFOLIO ENGINE BOOTSTRAP ............... <span class="ok">[READY]</span>' },
  { t:2900, txt:'> RISK MANAGEMENT MODULE ................... <span class="ok">[ARMED]</span>' },
  { t:3200, txt:'> SIGNAL PROCESSING PIPELINE ............... <span class="ok">[ONLINE]</span>' },
  { t:3500, txt:'> BROKER API HANDSHAKE ..................... <span class="warn">[STANDBY]</span>' },
  { t:3800, txt:'> AUTONOMOUS AGENT CORE ................... <span class="ok">[LOADED]</span>' },
  { t:4100, txt:'> SCANNING ASX UNIVERSE (2,200+ TICKERS) .. <span class="info">[CACHED]</span>' },
  { t:4400, txt:'> SENTIMENT ANALYSIS ENGINE ................ <span class="ok">[READY]</span>' },
  { t:4700, txt:'> WEBSOCKET CHANNELS ...................... <span class="ok">[OPEN]</span>' },
  { t:5000, txt:'> COMMAND & CONTROL INTERFACE .............. <span class="ok">[ONLINE]</span>' },
  { t:5300, txt:'> STOP-LOSS / TAKE-PROFIT MONITOR ......... <span class="ok">[ARMED]</span>' },
  { t:5600, txt:'> FIREWALL RULES .......................... <span class="ok">[ENFORCED]</span>' },
  { t:5900, txt:'> SYSTEM INTEGRITY CHECK .................. <span class="ok">[PASS]</span>' },
  { t:6200, txt:'<span class="bright">> ALL SUBSYSTEMS OPERATIONAL</span>' },
  { t:6500, txt:'<span class="bright">> LAUNCHING DALIOS INTERFACE...</span>' },
];

const totalLines = lines.length;
lines.forEach((l, i) => {
  setTimeout(() => {
    const div = document.createElement('div');
    div.className = 'line';
    div.innerHTML = l.txt;
    log.appendChild(div);
    // Auto-scroll
    log.scrollTop = log.scrollHeight;
    // Progress
    const pct = Math.round(((i+1) / totalLines) * 100);
    pbar.style.width = pct + '%';
    ptxt.textContent = pct + '%';
    if (pct > 50) sLeft.textContent = 'SUBSYSTEMS: LOADING';
    if (pct > 90) sLeft.textContent = 'SUBSYSTEMS: ONLINE';
  }, l.t);
});

// Signal ready after boot sequence
setTimeout(() => {
  ptxt.textContent = 'SYSTEM READY';
  ptxt.style.textShadow = '0 0 8px #00ff41';
  if (window.pywebview) {
    window.pywebview.api.boot_complete();
  }
}, 7000);
</script>
</body>
</html>
"""


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
        # Make sure server is actually ready before navigating
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

    # Create window with splash screen
    window = webview.create_window(
        title="DALIOS — Automated Trading Framework",
        html=SPLASH_HTML,
        width=1400,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        text_select=True,
    )

    # Expose API for splash -> app transition
    api = Api(window, port)
    window.expose(api.boot_complete)

    webview.start()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
