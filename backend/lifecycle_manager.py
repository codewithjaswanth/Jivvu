"""
QuizZy Deterministic Backend Process & Lifecycle Manager.
Idempotently starts, stops, monitors, and tracks the backend.app:app process on 127.0.0.1:8000.
"""

from __future__ import annotations

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quizzy.lifecycle")

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(__file__).resolve().parent / "runtime_state.json"
PORT = 8000
LEASE_TIMEOUT_SECONDS = 45.0
HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
SHUTDOWN_URL = f"http://127.0.0.1:{PORT}/shutdown"

_lock = threading.Lock()


def _hidden_subprocess_kwargs() -> dict:
    """Return platform-specific kwargs to ensure child processes create zero console windows."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": si,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "stdin": subprocess.DEVNULL,
    }


def find_pythonw_exe() -> Path:
    """Resolve pythonw.exe in virtual environment for purely windowless daemon execution."""
    candidates = [
        REPO_ROOT / ".venv" / "Scripts" / "pythonw.exe",
        REPO_ROOT.parent / ".venv" / "Scripts" / "pythonw.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return find_python_exe()


def get_default_state() -> Dict[str, Any]:
    return {
        "pid": None,
        "started_by": None,
        "start_time": 0.0,
        "last_heartbeat": 0.0,
        "active_ccbp_tabs": 0,
        "online": False,
    }


def get_state() -> Dict[str, Any]:
    """Read the runtime state safely with fallback to default state."""
    with _lock:
        if not STATE_FILE.is_file():
            return get_default_state()
        try:
            content = STATE_FILE.read_text(encoding="utf-8").strip()
            if not content:
                return get_default_state()
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            return get_default_state()
        except Exception as exc:
            logger.warning("Failed to read runtime state: %s", exc)
            return get_default_state()


def save_state(state: Dict[str, Any]) -> None:
    """Atomically persist runtime state to disk."""
    with _lock:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = STATE_FILE.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp_path.replace(STATE_FILE)
        except Exception as exc:
            logger.warning("Failed to write runtime state: %s", exc)


def is_port_bound(port: int = PORT) -> bool:
    """Check if TCP port is accepting connections or listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.05)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def is_port_free_to_bind(port: int = PORT) -> bool:
    """Check if TCP port is completely free to bind without socket collisions."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def is_backend_healthy(timeout: float = 0.4) -> bool:
    """Ping http://127.0.0.1:8000/health with a short timeout."""
    if not is_port_bound(PORT):
        return False
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "QuizZy-Lifecycle/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_pids_on_port(port: int = PORT) -> List[int]:
    """Find process IDs currently bound to or listening on the target TCP port."""
    pids: List[int] = []
    try:
        kwargs = _hidden_subprocess_kwargs()
        res = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and "LISTENING" in parts:
                local_addr = parts[1]
                if f":{port}" in local_addr:
                    try:
                        pid = int(parts[-1])
                        if pid > 4 and pid not in pids:
                            pids.append(pid)
                    except ValueError:
                        pass
    except Exception as exc:
        logger.warning("netstat check failed: %s", exc)
    return pids


def is_safe_to_kill(pid: int) -> bool:
    """Verify that process is a recognized python or uvicorn runtime, never system/shell."""
    if pid <= 4:
        return False
    try:
        kwargs = _hidden_subprocess_kwargs()
        res = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
        out = res.stdout.lower()
        allowed = ("python", "pythonw", "uvicorn")
        for a in allowed:
            if a in out:
                return True
        return False
    except Exception:
        return False


def clean_port_8000() -> None:
    """Forcefully terminate any safe dev processes holding port 8000."""
    pids = get_pids_on_port(PORT)
    kwargs = _hidden_subprocess_kwargs()
    for pid in pids:
        if is_safe_to_kill(pid):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **kwargs,
                )
            except Exception:
                pass


def find_python_exe() -> Path:
    """Resolve Python executable in project venv (prefer pythonw.exe for windowless execution)."""
    candidates = [
        REPO_ROOT / ".venv" / "Scripts" / "pythonw.exe",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT.parent / ".venv" / "Scripts" / "pythonw.exe",
        REPO_ROOT.parent / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return Path(sys.executable)


def start_backend(source: str = "ccbp_auto") -> Dict[str, Any]:
    """
    Idempotently start the QuizZy backend on 127.0.0.1:8000.
    Returns JSON status with online=True when ready.
    """
    current_state = get_state()

    # 1. Idempotency Check: if already healthy, update heartbeat and return
    if is_backend_healthy():
        current_state["online"] = True
        current_state["last_heartbeat"] = time.time()
        if source and source != "heartbeat":
            current_state["started_by"] = source
        save_state(current_state)
        return {
            "status": "already_running",
            "online": True,
            "running": True,
            "port": PORT,
            "pid": current_state.get("pid"),
        }

    # 2. If port is bound by a dead or unresponsive process, clean it up and wait until free to bind
    if not is_port_free_to_bind(PORT) or is_port_bound(PORT):
        logger.info("Port %d is bound or in TIME_WAIT. Cleaning port...", PORT)
        clean_port_8000()
        t_clean = time.time()
        while time.time() - t_clean < 2.5:
            if is_port_free_to_bind(PORT):
                break
            time.sleep(0.1)

    # 3. Resolve Python executable (prefer pythonw.exe for windowless execution)
    python_exe = find_pythonw_exe()
    cmd = [
        str(python_exe),
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        "--log-level",
        "warning",
    ]

    # 4. Windows creation flags & startupinfo: windowless detached process group
    hidden_kwargs = _hidden_subprocess_kwargs()
    creationflags = hidden_kwargs.get("creationflags", 0)
    if sys.platform == "win32":
        # 0x00000008 = DETACHED_PROCESS, ensures child survives parent exit
        creationflags |= (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008)
    startupinfo = hidden_kwargs.get("startupinfo")

    # Clean pytest test markers from child environment so it runs in clean production mode
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as exc:
        logger.error("Failed to spawn backend: %s", exc)
        return {
            "status": "error",
            "error": f"Failed to spawn backend process: {exc}",
            "online": False,
            "port": PORT,
        }

    # 5. Poll /health until HTTP 200 (every 150ms up to 8s)
    t0 = time.time()
    online = False
    while time.time() - t0 < 8.0:
        if is_backend_healthy(timeout=0.3):
            online = True
            break
        time.sleep(0.15)

    if online:
        new_state = {
            "pid": proc.pid,
            "started_by": source,
            "start_time": time.time(),
            "last_heartbeat": time.time(),
            "active_ccbp_tabs": max(1, current_state.get("active_ccbp_tabs", 1)),
            "online": True,
        }
        save_state(new_state)
        return {
            "status": "started",
            "pid": proc.pid,
            "online": True,
            "running": True,
            "port": PORT,
        }
    else:
        # Failed to boot within 8 seconds
        return {
            "status": "error",
            "error": "Backend process spawned but did not respond on /health within 8 seconds",
            "pid": proc.pid,
            "online": False,
            "running": False,
            "port": PORT,
        }


def stop_backend(reason: str = "ccbp_closed", source: Optional[str] = None) -> Dict[str, Any]:
    """
    Idempotently stop the QuizZy backend on 127.0.0.1:8000.
    Releases port 8000 completely with zero errors.
    """
    actual_reason = source or reason
    # 1. Idempotency Check: if already stopped and port free, reset state and return
    if not is_backend_healthy() and (not is_port_bound(PORT) or is_port_free_to_bind(PORT)):
        reset_state = get_default_state()
        save_state(reset_state)
        return {
            "status": "already_stopped",
            "online": False,
            "running": False,
            "port": PORT,
            "reason": actual_reason,
        }

    # 2. Attempt graceful HTTP /shutdown endpoint first
    try:
        req = urllib.request.Request(
            SHUTDOWN_URL,
            data=b"{}",
            headers={"Content-Type": "application/json", "User-Agent": "QuizZy-Lifecycle/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=0.8):
            pass
    except Exception:
        pass

    # Give process 1.0s to terminate gracefully
    t0 = time.time()
    while time.time() - t0 < 1.0:
        if not is_port_bound(PORT) and not is_backend_healthy():
            break
        time.sleep(0.1)

    # 3. Clean any remaining PIDs bound to port 8000
    state = get_state()
    tracked_pid = state.get("pid")
    pids = get_pids_on_port(PORT)
    if tracked_pid and tracked_pid not in pids and is_safe_to_kill(tracked_pid):
        pids.append(tracked_pid)

    kwargs = _hidden_subprocess_kwargs()
    for pid in pids:
        if is_safe_to_kill(pid):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **kwargs,
                )
            except Exception:
                pass
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

    # 4. Verify port is completely free to bind (poll up to 3.5 seconds)
    t0 = time.time()
    released = False
    while time.time() - t0 < 3.5:
        if not is_backend_healthy() and is_port_free_to_bind(PORT):
            released = True
            break
        time.sleep(0.1)

    reset_state = get_default_state()
    save_state(reset_state)

    return {
        "status": "stopped",
        "online": False,
        "running": False,
        "port": PORT,
        "released": released,
    }


def record_heartbeat(active_tabs: int = 1, url: str = "") -> Dict[str, Any]:
    """
    Record heartbeat from active learning.ccbp.in tabs.
    Recovers backend if tabs > 0 but backend is offline.
    """
    state = get_state()
    state["last_heartbeat"] = time.time()
    state["active_ccbp_tabs"] = max(0, int(active_tabs))

    healthy = is_backend_healthy()
    state["online"] = healthy

    # Heartbeat Recovery: if active tabs exist but backend crashed/stopped, resurrect it
    if state["active_ccbp_tabs"] > 0 and not healthy:
        logger.info("Heartbeat recovery triggered for %d active tab(s)", state["active_ccbp_tabs"])
        res = start_backend(source="heartbeat_recovery")
        state["online"] = res.get("online", False)
        if res.get("pid"):
            state["pid"] = res["pid"]

    save_state(state)
    return {
        "status": "ok",
        "online": state["online"],
        "active_tabs": state["active_ccbp_tabs"],
        "active_ccbp_tabs": state["active_ccbp_tabs"],
        "last_heartbeat": state["last_heartbeat"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="QuizZy Backend Lifecycle Manager")
    parser.add_argument("--start", action="store_true", help="Start the backend daemon")
    parser.add_argument("--stop", action="store_true", help="Stop the backend daemon")
    parser.add_argument("--status", action="store_true", help="Check backend status")
    parser.add_argument("--heartbeat", action="store_true", help="Record heartbeat")
    parser.add_argument("--tabs", type=int, default=1, help="Active CCBP tabs for heartbeat")
    args = parser.parse_args(argv)

    if args.start:
        res = start_backend(source="cli")
        print(json.dumps(res))
        return 0 if res.get("online") else 1
    elif args.stop:
        res = stop_backend(reason="cli_stop")
        print(json.dumps(res))
        return 0
    elif args.heartbeat:
        res = record_heartbeat(active_tabs=args.tabs)
        print(json.dumps(res))
        return 0
    elif args.status:
        state = get_state()
        state["online"] = is_backend_healthy()
        print(json.dumps(state))
        return 0 if state["online"] else 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())

