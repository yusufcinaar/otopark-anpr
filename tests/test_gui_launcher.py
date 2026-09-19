"""Windowless startup, diagnostics and the real BAT launch, without the real app."""
import ctypes
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
import venv

import pytest

from app import gui_launcher


@pytest.fixture(autouse=True)
def isolated_db():
    """These launcher tests must not initialize even a test application DB."""
    yield None


def _fake_entrypoint(monkeypatch, callback):
    module = ModuleType("app.main")
    module.main = callback
    monkeypatch.setitem(sys.modules, "app.main", module)


def _capture_native_message(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ctypes, "windll",
        SimpleNamespace(user32=SimpleNamespace(
            MessageBoxW=lambda *args: calls.append(args))),
        raising=False,
    )
    return calls


def test_pythonw_none_streams_capture_output_and_preserve_previous_log(tmp_path, monkeypatch):
    monkeypatch.setenv("OTOPARK_DATA_DIR", str(tmp_path))
    log = tmp_path / "logs" / "uygulama.log"
    log.parent.mkdir()
    log.write_text("previous launch\n", encoding="utf-8")
    native_messages = _capture_native_message(monkeypatch)

    def fake_main():
        print("Plaka 34ABC123")
        print("Tanilama: baglanti bekleniyor", file=sys.stderr)
        logging.getLogger("otopark").info("CIKIS-1 kamera baglantisi hazir")
        return 0

    _fake_entrypoint(monkeypatch, fake_main)
    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "stdout", None)
        no_console.setattr(sys, "stderr", None)
        assert gui_launcher.main() == 0
        assert sys.stdout is None and sys.stderr is None

    text = log.read_text(encoding="utf-8")
    assert "previous launch" in text
    assert "Plaka 34ABC123" in text
    assert "Tanilama: baglanti bekleniyor" in text
    assert "INFO | CIKIS-1 kamera baglantisi hazir" in text
    assert "PROGRAM BASLATILIYOR" in text and "PROGRAM KAPANIYOR" in text
    assert native_messages == []


def test_import_failure_before_qt_is_logged_and_shown_natively(tmp_path, monkeypatch):
    monkeypatch.setenv("OTOPARK_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "app.main", None)
    messages = _capture_native_message(monkeypatch)
    previous_streams = sys.stdout, sys.stderr

    assert gui_launcher.main() == 1

    assert (sys.stdout, sys.stderr) == previous_streams
    log = tmp_path / "logs" / "uygulama.log"
    assert "ModuleNotFoundError" in log.read_text(encoding="utf-8")
    assert len(messages) == 1
    assert "app.main" in messages[0][1]
    assert str(log) in messages[0][1]
    assert "kur.bat" in messages[0][1]


def test_application_exception_keeps_traceback_and_restores_streams(tmp_path, monkeypatch):
    monkeypatch.setenv("OTOPARK_DATA_DIR", str(tmp_path))
    messages = _capture_native_message(monkeypatch)
    previous_streams = sys.stdout, sys.stderr

    def fake_main():
        raise RuntimeError("Baslangic testi hatasi")

    _fake_entrypoint(monkeypatch, fake_main)
    assert gui_launcher.main() == 1
    assert (sys.stdout, sys.stderr) == previous_streams
    log_text = (tmp_path / "logs" / "uygulama.log").read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: Baslangic testi hatasi" in log_text
    assert "Baslangic testi hatasi" in messages[0][1]


@pytest.mark.parametrize("exit_code", [0, 2])
def test_system_exit_retains_application_status_without_error_dialog(tmp_path, monkeypatch, exit_code):
    monkeypatch.setenv("OTOPARK_DATA_DIR", str(tmp_path))
    messages = _capture_native_message(monkeypatch)
    previous_streams = sys.stdout, sys.stderr

    def fake_main():
        print("Normal application exit")
        raise SystemExit(exit_code)

    _fake_entrypoint(monkeypatch, fake_main)
    with pytest.raises(SystemExit) as result:
        gui_launcher.main()
    assert result.value.code == exit_code
    assert (sys.stdout, sys.stderr) == previous_streams
    assert messages == []
    assert "Normal application exit" in (tmp_path / "logs" / "uygulama.log").read_text(encoding="utf-8")


def test_log_directory_failure_is_visible_without_importing_application(tmp_path, monkeypatch):
    invalid_data = tmp_path / "not-a-directory"
    invalid_data.write_text("file", encoding="utf-8")
    monkeypatch.setenv("OTOPARK_DATA_DIR", str(invalid_data))
    messages = _capture_native_message(monkeypatch)
    started = []
    _fake_entrypoint(monkeypatch, lambda: started.append(True))
    previous_streams = sys.stdout, sys.stderr

    assert gui_launcher.main() == 1

    assert started == []
    assert (sys.stdout, sys.stderr) == previous_streams
    assert len(messages) == 1
    assert str(invalid_data) in messages[0][1]


def _wait_for_file(path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.05)
    return path.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows BAT/pythonw integration")
def test_run_bat_exits_while_windowless_application_remains_running(tmp_path):
    """Run the delivered BAT against an isolated stdlib-only fake application."""
    source_root = Path(__file__).resolve().parents[1]
    package = tmp_path / "Otopark Test Program"
    package.mkdir()
    shutil.copy2(source_root / "run.bat", package / "run.bat")
    (package / "kur.bat").write_text(
        "@echo off\r\necho unexpected installation>installation_attempted.txt\r\nexit /b 99\r\n",
        encoding="ascii",
    )
    (package / ".env.example").write_text("# isolated launcher probe\n", encoding="ascii")
    app = package / "app"
    app.mkdir()
    shutil.copy2(source_root / "app" / "gui_launcher.py", app / "gui_launcher.py")
    # Any unexpected startup error is captured to a local file, never real UI.
    (app / "__init__.py").write_text(
        "import ctypes, os\nfrom pathlib import Path\n"
        "def record_message(*args):\n"
        "    Path(os.environ['PROBE_ROOT'], 'native_message.txt').write_text(str(args), encoding='utf-8')\n"
        "    return 0\n"
        "ctypes.windll.user32.MessageBoxW = record_message\n",
        encoding="utf-8",
    )
    (app / "db.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def init_db():\n"
        "    Path(os.environ['PROBE_ROOT'], 'db_checked.txt').write_text('fake database only', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (app / "main.py").write_text(
        "import ctypes, json, os, sys, time\nfrom pathlib import Path\nfrom app import db\n"
        "def main():\n"
        "    root = Path(os.environ['PROBE_ROOT'])\n"
        "    db.init_db()\n"
        "    print('FAKE_APPLICATION_STDOUT')\n"
        "    print('FAKE_APPLICATION_STDERR', file=sys.stderr)\n"
        "    state = {'pid': os.getpid(), 'console': ctypes.windll.kernel32.GetConsoleWindow(), 'executable': sys.executable}\n"
        "    (root / 'ready.json').write_text(json.dumps(state), encoding='utf-8')\n"
        "    deadline = time.monotonic() + 20\n"
        "    while not (root / 'release').exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.05)\n"
        "    (root / 'finished').write_text('done', encoding='utf-8')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    local_app_data = tmp_path / "Local App Data"
    runtime = local_app_data / "OtoparkANPRPublic" / "runtime" / ".venv-py312"
    venv.EnvBuilder(with_pip=False).create(runtime)
    assert (runtime / "Scripts" / "pythonw.exe").is_file()
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local_app_data),
        "OTOPARK_DATA_DIR": str(tmp_path / "probe-data"),
        "PROBE_ROOT": str(tmp_path),
    })
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "run.bat"]
    with (tmp_path / "bat-output.txt").open("w", encoding="utf-8") as output:
        batch = subprocess.Popen(command, cwd=package, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            assert batch.wait(timeout=10) == 0
            assert _wait_for_file(tmp_path / "ready.json")
            state = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
            assert state["console"] == 0
            assert Path(state["executable"]).name.lower() == "pythonw.exe"
            assert not (tmp_path / "finished").exists()
            assert (tmp_path / "db_checked.txt").is_file()
            assert not (package / "installation_attempted.txt").exists()
            assert not (tmp_path / "native_message.txt").exists()
            log = Path(env["OTOPARK_DATA_DIR"]) / "logs" / "uygulama.log"
            log_text = log.read_text(encoding="utf-8")
            assert "FAKE_APPLICATION_STDOUT" in log_text
            assert "FAKE_APPLICATION_STDERR" in log_text
        finally:
            (tmp_path / "release").touch()
            if batch.poll() is None:
                batch.kill()
                batch.wait(timeout=5)
            if (tmp_path / "ready.json").exists():
                assert _wait_for_file(tmp_path / "finished", timeout=5), "Fake application did not exit"
