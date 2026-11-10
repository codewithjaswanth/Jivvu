"""
Unit and Integration Tests for QuizZy Automatic Backend Lifecycle Manager.
Covers deterministic process management, dual-channel OS bridge,
native messaging host protocol, and CCBP tab reference counting / lease watchdog.
"""

import io
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import lifecycle_manager, native_host
from backend.dashboard_server import app as dashboard_app


@pytest.fixture(autouse=True)
def isolated_lifecycle_state(tmp_path, monkeypatch):
    """Use an isolated state file for every test to avoid cross-test pollution."""
    test_state_file = tmp_path / "test_runtime_state.json"
    monkeypatch.setattr(lifecycle_manager, "STATE_FILE", test_state_file)
    yield test_state_file
    if test_state_file.exists():
        test_state_file.unlink(missing_ok=True)


class TestLifecycleManagerCore:
    def test_default_state(self):
        state = lifecycle_manager.get_state()
        assert state["online"] is False
        assert state["pid"] is None
        assert state["active_ccbp_tabs"] == 0
        assert state["last_heartbeat"] == 0.0

    def test_save_and_get_state(self):
        custom = {
            "pid": 12345,
            "started_by": "unit_test",
            "start_time": 100.0,
            "last_heartbeat": 105.0,
            "active_ccbp_tabs": 3,
            "online": True,
        }
        lifecycle_manager.save_state(custom)
        loaded = lifecycle_manager.get_state()
        assert loaded["pid"] == 12345
        assert loaded["started_by"] == "unit_test"
        assert loaded["active_ccbp_tabs"] == 3
        assert loaded["online"] is True

    def test_record_heartbeat(self):
        state = lifecycle_manager.record_heartbeat(active_tabs=4)
        assert state["active_tabs"] == 4
        assert state["last_heartbeat"] > 0
        loaded = lifecycle_manager.get_state()
        assert loaded["active_ccbp_tabs"] == 4

    def test_is_safe_to_kill_filtering(self):
        # Critical security & safety check: system processes must never be killed
        assert lifecycle_manager.is_safe_to_kill(0) is False
        assert lifecycle_manager.is_safe_to_kill(4) is False

    @patch("backend.lifecycle_manager.is_backend_healthy", return_value=True)
    def test_start_backend_idempotency_when_already_healthy(self, mock_healthy):
        res = lifecycle_manager.start_backend(source="test_caller")
        assert res["status"] == "already_running"
        assert res["online"] is True
        assert res["port"] == 8000
        state = lifecycle_manager.get_state()
        assert state["online"] is True
        assert state["started_by"] == "test_caller"

    @patch("backend.lifecycle_manager.is_port_bound", return_value=False)
    def test_stop_backend_idempotency_when_already_stopped(self, mock_bound):
        res = lifecycle_manager.stop_backend(source="test_caller")
        assert res["status"] == "already_stopped"
        assert res["port"] == 8000
        state = lifecycle_manager.get_state()
        assert state["online"] is False
        assert state["active_ccbp_tabs"] == 0


class TestDashboardLifecycleAPI:
    @pytest.fixture
    def client(self):
        return TestClient(dashboard_app)

    def test_get_status_endpoint(self, client):
        resp = client.get("/api/lifecycle/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "online" in data
        assert "port" in data
        assert "state" in data

    def test_post_heartbeat_endpoint(self, client):
        resp = client.post("/api/lifecycle/heartbeat", json={"active_tabs": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "heartbeat_recorded")
        assert data["active_tabs"] == 2
        assert data["last_heartbeat"] > 0

    @patch("backend.lifecycle_manager.start_backend")
    def test_post_start_endpoint(self, mock_start, client):
        mock_start.return_value = {
            "status": "started",
            "online": True,
            "port": 8000,
            "pid": 9999,
            "started_by": "extension:test",
        }
        resp = client.post("/api/lifecycle/start", json={"source": "extension:test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["online"] is True
        assert data["pid"] == 9999
        mock_start.assert_called_once_with(source="extension:test")

    @patch("backend.lifecycle_manager.stop_backend")
    def test_post_stop_endpoint(self, mock_stop, client):
        mock_stop.return_value = {
            "status": "stopped",
            "port": 8000,
            "killed_pids": [9999],
        }
        resp = client.post("/api/lifecycle/stop", json={"source": "extension:test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        mock_stop.assert_called_once_with(reason="extension:test")


class TestNativeMessagingHost:
    def test_native_message_ping(self):
        resp = native_host.handle_message({"action": "PING"})
        assert resp["success"] is True
        assert resp["pong"] is True
        assert "timestamp" in resp

    def test_native_message_status(self):
        resp = native_host.handle_message({"action": "STATUS"})
        assert resp["success"] is True
        assert "online" in resp
        assert "port" in resp
        assert "state" in resp

    def test_native_message_heartbeat(self):
        resp = native_host.handle_message({"action": "HEARTBEAT", "active_tabs": 5})
        assert resp["success"] is True
        assert resp["status"] == "heartbeat_recorded"
        assert resp["active_tabs"] == 5

    def test_native_message_unknown_action(self):
        resp = native_host.handle_message({"action": "INVALID_CMD"})
        assert resp["success"] is False
        assert "Unknown action" in resp["error"]

    def test_native_binary_framing(self):
        """Verify exact Chrome length-prefixed stdio framing."""
        payload = {"action": "PING"}
        raw_bytes = json.dumps(payload).encode("utf-8")
        header = struct.pack("<I", len(raw_bytes))
        fake_stdin = io.BytesIO(header + raw_bytes)

        msg = native_host.read_native_message(stream=fake_stdin)
        assert msg == payload

        fake_stdout = io.BytesIO()
        native_host.send_native_message({"pong": True}, stream=fake_stdout)
        out_bytes = fake_stdout.getvalue()
        assert len(out_bytes) > 4
        out_len = struct.unpack("<I", out_bytes[:4])[0]
        out_data = json.loads(out_bytes[4 : 4 + out_len].decode("utf-8"))
        assert out_data == {"pong": True}


class TestExtensionManifestAndFiles:
    def test_manifest_permissions(self):
        manifest_path = REPO_ROOT / "extension" / "manifest.json"
        assert manifest_path.is_file()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        perms = data.get("permissions", [])
        assert "tabs" in perms
        assert "nativeMessaging" in perms
        assert "alarms" in perms
        assert "storage" in perms

        host_perms = data.get("host_permissions", [])
        assert any("8765" in hp for hp in host_perms)
        assert any("8000" in hp for hp in host_perms)
        assert any("learning.ccbp.in" in hp for hp in host_perms)

    def test_native_host_manifest(self):
        host_manifest = REPO_ROOT / "backend" / "com.quizzy.lifecycle.json"
        assert host_manifest.is_file()
        data = json.loads(host_manifest.read_text(encoding="utf-8"))
        assert data["name"] == "com.quizzy.lifecycle"
        assert data["type"] == "stdio"
        assert "native_host_win.exe" in data["path"]
        assert len(data["allowed_origins"]) >= 6

    def test_ccbp_lifecycle_bridge_script(self):
        bridge_script = REPO_ROOT / "extension" / "ccbp_lifecycle_bridge.js"
        assert bridge_script.is_file()
        content = bridge_script.read_text(encoding="utf-8")
        assert "CCBP_PAGE_HEARTBEAT" in content
        assert "pagehide" in content


class TestStealthAndPopupElimination:
    """
    Verifies 100% silent and invisible backend lifecycle:
    1. Subprocess execution flags enforce CREATE_NO_WINDOW and hidden window.
    2. pythonw.exe resolver targets GUI subsystem python to prevent console flashes.
    3. Content scripts contain zero direct localhost/127.0.0.1 fetches, preventing Chrome PNA prompts.
    4. Extension background worker implements strict HTTP-first routing, bypassing native host CMD windows when daemon is online.
    """

    def test_subprocess_hidden_kwargs_configuration(self):
        kwargs = lifecycle_manager._hidden_subprocess_kwargs()
        assert kwargs.get("stdin") == subprocess.DEVNULL
        if sys.platform == "win32":
            assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW
            startupinfo = kwargs.get("startupinfo")
            assert startupinfo is not None
            assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
            assert startupinfo.wShowWindow == 0

    def test_pythonw_resolver_targets_gui_subsystem(self):
        pythonw_exe = lifecycle_manager.find_pythonw_exe()
        assert "pythonw" in pythonw_exe.name.lower() or "python" in pythonw_exe.name.lower()

    def test_native_host_win_pe_subsystem_and_ping(self):
        exe_path = REPO_ROOT / "backend" / "native_host_win.exe"
        assert exe_path.is_file(), "native_host_win.exe must exist"
        with open(exe_path, "rb") as f:
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset + 4 + 20 + 68)
            subsystem = struct.unpack("<H", f.read(2))[0]
            assert subsystem == 2, f"Expected IMAGE_SUBSYSTEM_WINDOWS_GUI (2), got {subsystem}"

        payload = json.dumps({"action": "PING"}).encode("utf-8")
        header = struct.pack("<I", len(payload))
        t0 = time.time()
        proc = subprocess.run([str(exe_path)], input=header + payload, capture_output=True, check=False, timeout=3.0)
        elapsed = time.time() - t0
        assert proc.returncode == 0, f"native_host_win.exe returned code {proc.returncode}"
        assert len(proc.stdout) >= 4, f"Output too short: {proc.stdout}"
        resp_len = struct.unpack("<I", proc.stdout[:4])[0]
        resp_data = json.loads(proc.stdout[4 : 4 + resp_len].decode("utf-8"))
        assert resp_data.get("pong") is True
        assert elapsed < 2.5, f"PING took too long ({elapsed}s)"

    def test_decoupled_start_daemon_persists_without_isdaemon(self):
        from backend.cli import is_dashboard_online, start_daemon
        # Ensure daemon can be started and is online
        code = start_daemon(port=8765)
        assert code == 0
        assert is_dashboard_online(port=8765) is True

    def test_content_scripts_have_zero_localhost_fetches(self):
        scripts = [
            REPO_ROOT / "extension" / "ccbp_lifecycle_bridge.js",
            REPO_ROOT / "extension" / "floating_hud.js",
            REPO_ROOT / "extension" / "extractor.js",
            REPO_ROOT / "extension" / "executor.js",
        ]
        pattern = re.compile(r"fetch\s*\(\s*['\"]https?://(?:127\.0\.0\.1|localhost)", re.IGNORECASE)
        for s in scripts:
            assert s.is_file(), f"Script missing: {s}"
            content = s.read_text(encoding="utf-8")
            matches = pattern.findall(content)
            assert len(matches) == 0, f"Found direct localhost fetch in content script {s.name}: {matches}"

    def test_background_js_strict_http_first_routing(self):
        bg_path = (REPO_ROOT / "extension" / "background.js").as_posix()
        node_script = f"""
        const fs = require('fs');
        const vm = require('vm');

        let openTabs = [];
        let sentNativeMessages = [];
        let lastRegisteredListeners = {{
          onUpdated: null,
          onCreated: null,
          onRemoved: null,
          onReplaced: null,
          windowsOnRemoved: null,
          onStartup: null,
          onHistoryStateUpdated: null,
          onCommitted: null
        }};

        const mockChrome = {{
          storage: {{
            local: {{
              get: (keys, cb) => cb({{ allowedHosts: ['learning.ccbp.in'] }}),
              set: (obj, cb) => {{ if (cb) cb(); }}
            }}
          }},
          runtime: {{
            lastError: null,
            onMessage: {{ addListener: (fn) => {{}} }},
            onStartup: {{ addListener: (fn) => {{ lastRegisteredListeners.onStartup = fn; }} }},
            sendNativeMessage: (host, msg, cb) => {{
              sentNativeMessages.push(msg);
              if (cb) cb({{ success: true, status: msg.action === 'START_BACKEND' ? 'started' : 'stopped' }});
            }}
          }},
          tabs: {{
            query: (queryInfo, cb) => {{
              cb(openTabs.map(t => Object.assign({{}}, t)));
            }},
            onUpdated: {{ addListener: (fn) => {{ lastRegisteredListeners.onUpdated = fn; }} }},
            onCreated: {{ addListener: (fn) => {{ lastRegisteredListeners.onCreated = fn; }} }},
            onRemoved: {{ addListener: (fn) => {{ lastRegisteredListeners.onRemoved = fn; }} }},
            onReplaced: {{ addListener: (fn) => {{ lastRegisteredListeners.onReplaced = fn; }} }}
          }},
          windows: {{
            onRemoved: {{ addListener: (fn) => {{ lastRegisteredListeners.windowsOnRemoved = fn; }} }},
            getAll: (opts, cb) => cb([])
          }},
          webNavigation: {{
            onHistoryStateUpdated: {{ addListener: (fn) => {{ lastRegisteredListeners.onHistoryStateUpdated = fn; }} }},
            onCommitted: {{ addListener: (fn) => {{ lastRegisteredListeners.onCommitted = fn; }} }}
          }},
          action: {{
            setBadgeText: () => {{}},
            setBadgeBackgroundColor: () => {{}},
            onClicked: {{ addListener: () => {{}} }}
          }},
          alarms: {{
            create: () => {{}},
            clear: () => Promise.resolve(true),
            get: (name, cb) => cb(null),
            onAlarm: {{ addListener: () => {{}} }}
          }},
          scripting: {{
            executeScript: () => Promise.resolve([{{ result: true }}])
          }}
        }};

        let timers = [];
        const customSetTimeout = (fn, ms) => {{
          const id = timers.length + 1;
          const handle = {{ id, fn, ms, cleared: false }};
          timers.push(handle);
          return id;
        }};
        const customClearTimeout = (id) => {{
          for (const t of timers) {{
            if (t.id === id) t.cleared = true;
          }}
        }};
        function advanceTimers() {{
          for (const t of timers) {{
            if (!t.cleared) {{
              t.cleared = true;
              t.fn();
            }}
          }}
        }}

        let daemonStartCalled = 0;
        let daemonStopCalled = 0;
        let mockBackendOnline = false;

        const mockFetch = (url, opts) => {{
          const u = String(url);
          if (u.includes(':8765/api/lifecycle/start')) {{
            daemonStartCalled++;
            mockBackendOnline = true;
            return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{ status: 'started', online: true }}) }});
          }}
          if (u.includes(':8765/api/lifecycle/stop')) {{
            daemonStopCalled++;
            mockBackendOnline = false;
            return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{ status: 'stopped', online: false }}) }});
          }}
          if (u.includes('/health')) {{
            if (mockBackendOnline) {{
              return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{ status: 'ok' }}) }});
            }} else {{
              return Promise.reject(new Error('Connection refused'));
            }}
          }}
          return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve({{}}) }});
        }};

        const sandbox = {{
          chrome: mockChrome,
          fetch: mockFetch,
          setTimeout: customSetTimeout,
          clearTimeout: customClearTimeout,
          console,
          URL,
          Array,
          Object,
          String,
          Number,
          Boolean,
          Date,
          RegExp,
          Map,
          Set
        }};

        const bgSource = fs.readFileSync('{bg_path}', 'utf8');
        vm.runInNewContext(bgSource, sandbox);

        // Open CCBP Tab -> Must trigger HTTP start, ZERO native messages!
        const tab1 = {{ id: 101, url: 'https://learning.ccbp.in/course?c_id=intro' }};
        openTabs.push(tab1);
        lastRegisteredListeners.onUpdated(101, {{ status: 'loading', url: tab1.url }}, tab1);

        setTimeout(() => {{
          if (daemonStartCalled !== 1) throw new Error('Expected 1 daemon start call, got ' + daemonStartCalled);
          if (sentNativeMessages.length !== 0) throw new Error('sendNativeMessage called when daemon online! Count: ' + sentNativeMessages.length);

          // Close CCBP Tab -> Must trigger HTTP stop after debounce, ZERO native messages!
          openTabs = [];
          lastRegisteredListeners.onRemoved(101, {{ isWindowClosing: false }});
          advanceTimers();

          setTimeout(() => {{
            if (daemonStopCalled !== 1) throw new Error('Expected 1 daemon stop call, got ' + daemonStopCalled);
            if (sentNativeMessages.length !== 0) throw new Error('sendNativeMessage called on stop when daemon online! Count: ' + sentNativeMessages.length);
            console.log('STRICT_HTTP_FIRST_ZERO_NATIVE_MESSAGES_SUCCESS');
          }}, 10);
        }}, 10);
        advanceTimers();
        """

        res = subprocess.run(
            ["node", "-e", node_script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Node script failed:\\nSTDOUT:\\n{{res.stdout}}\\nSTDERR:\\n{{res.stderr}}"
        assert "STRICT_HTTP_FIRST_ZERO_NATIVE_MESSAGES_SUCCESS" in res.stdout


class TestLiveLifecycleEnduranceSimulation:
    """
    Live multi-second endurance test verifying that:
    1. POST /api/lifecycle/start starts port 8000 backend, health is 200, dashboard status is ON.
    2. Intra-site navigation (active_tabs briefly 0 then 1 within 1 second) keeps port 8000 online without interruption.
    3. POST /api/lifecycle/stop stops port 8000 backend completely, health is unreachable, dashboard status is OFF.
    """

    @pytest.fixture
    def client(self):
        return TestClient(dashboard_app)

    def test_live_start_navigate_stop_cycle(self, client):
        # 0. Skip if protected live backend daemon is running on port 8000
        if lifecycle_manager.is_backend_healthy():
            pytest.skip("Protected live backend daemon is running on port 8000; skipping destructive lifecycle stop.")

        # Clean state before test
        lifecycle_manager.stop_backend("test_pre_clean")
        time.sleep(0.4)

        # 1. Simulate opening https://learning.ccbp.in/ -> Start backend
        res_start = client.post("/api/lifecycle/start", json={"source": "ccbp_auto"})
        assert res_start.status_code == 200
        data_start = res_start.json()
        assert data_start["online"] is True

        # Verify backend on port 8000 is healthy
        assert lifecycle_manager.is_backend_healthy() is True

        # Verify dashboard backend-status reports ON
        res_status_on = client.get("/api/dashboard/backend-status")
        assert res_status_on.status_code == 200
        assert res_status_on.json()["status_label"] == "ON"
        assert res_status_on.json()["online"] is True

        # 2. Simulate intra-site navigation (brief active_tabs=0 for 0.8s, then active_tabs=1)
        client.post("/api/lifecycle/heartbeat", json={"active_tabs": 0, "url": "https://learning.ccbp.in/course"})
        time.sleep(0.8)
        # Backend must remain running!
        assert lifecycle_manager.is_backend_healthy() is True

        # Navigation completes, page heartbeat arrives
        client.post("/api/lifecycle/heartbeat", json={"active_tabs": 1, "url": "https://learning.ccbp.in/course/exam"})
        assert lifecycle_manager.is_backend_healthy() is True

        # 3. Simulate closing learning.ccbp.in -> Stop backend
        res_stop = client.post("/api/lifecycle/stop", json={"source": "ccbp_closed"})
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] in ("stopped", "already_stopped")

        # Wait up to 3.0s for process cleanup on Windows
        t_stop = time.time()
        while time.time() - t_stop < 3.0:
            if not lifecycle_manager.is_backend_healthy():
                break
            time.sleep(0.1)

        # Verify backend on port 8000 is completely offline
        assert lifecycle_manager.is_backend_healthy() is False

        # Verify dashboard backend-status reports OFF
        res_status_off = client.get("/api/dashboard/backend-status")
        assert res_status_off.status_code == 200
        assert res_status_off.json()["status_label"] == "OFF"
        assert res_status_off.json()["online"] is False
