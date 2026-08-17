"""Start or stop the local Streamlit app from a Windows desktop shortcut."""

from __future__ import annotations

import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_FILE = PROJECT_ROOT / "app" / "app.py"
RUNTIME_DIR = PROJECT_ROOT / "data" / "temp"
PID_FILE = RUNTIME_DIR / "desktop_app.json"
LOG_FILE = RUNTIME_DIR / "desktop_app.log"
HOST = "127.0.0.1"
PORT = 8511
URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{URL}/_stcore/health"
QUIET = "--quiet" in sys.argv


def _message(title, text, error=False):
    if QUIET:
        return
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(0, str(text), str(title), flags)


def _healthy():
    try:
        with urlopen(HEALTH_URL, timeout=1) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except (OSError, URLError):
        return False


def _port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(.5)
        return connection.connect_ex((HOST, PORT)) == 0


def _read_pid():
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_pid(process):
    PID_FILE.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "port": PORT,
                "url": URL,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "python": sys.executable,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def start():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if _healthy():
        webbrowser.open(URL, new=2)
        return
    if _port_in_use():
        _message(
            "Planificador Multideporte",
            f"El puerto {PORT} está ocupado por otra aplicación. Revisa {LOG_FILE}.",
            error=True,
        )
        return
    creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_FILE),
        "--server.address",
        HOST,
        "--server.port",
        str(PORT),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Iniciando aplicación\n")
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            close_fds=True,
        )
    _write_pid(process)
    for _ in range(60):
        if _healthy():
            webbrowser.open(URL, new=2)
            return
        if process.poll() is not None:
            break
        time.sleep(.25)
    _message(
        "Planificador Multideporte",
        f"No fue posible iniciar la aplicación. Revisa el registro:\n{LOG_FILE}",
        error=True,
    )


def stop():
    payload = _read_pid()
    pid = payload.get("pid")
    if not isinstance(pid, int):
        if _healthy():
            _message(
                "Planificador Multideporte",
                "La aplicación está activa, pero no pertenece al lanzador actual.",
                error=True,
            )
        else:
            _message("Planificador Multideporte", "La aplicación ya está detenida.")
        return
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass
    if result.returncode == 0:
        _message("Planificador Multideporte", "La aplicación se detuvo correctamente.")
    elif not _healthy():
        _message("Planificador Multideporte", "La aplicación ya estaba detenida.")
    else:
        _message(
            "Planificador Multideporte",
            f"No fue posible detener el proceso {pid}.",
            error=True,
        )


if __name__ == "__main__":
    arguments = [item for item in sys.argv[1:] if item != "--quiet"]
    action = arguments[0].lower() if arguments else "start"
    if action == "start":
        start()
    elif action == "stop":
        stop()
    else:
        _message("Planificador Multideporte", f"Acción desconocida: {action}", error=True)
