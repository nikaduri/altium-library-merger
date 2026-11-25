#!/usr/bin/env python3
"""Launcher for Altium Library Merger Web Application.
Starts local web server and automatically opens browser.
"""

import sys
import os
import webbrowser
import threading
import time

from app import run_server


def open_browser(url: str):
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Could not automatically open browser: {e}")


def main():
    port = int(os.environ.get("PORT", 5000))
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    print("=" * 60)
    print("      ALTIUM SCHEMATIC & PCB LIBRARY MERGER APP")
    print(f"      Running at: {url}")
    print("=" * 60)
    print("Starting web interface and opening browser...")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    run_server(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
