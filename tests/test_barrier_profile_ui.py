"""Exercise the stale MOCK field regression in an isolated Qt GUI process."""
import os
from pathlib import Path
import subprocess
import sys


def test_saved_site_profile_survives_connection_test_and_reopen(tmp_path):
    script = r'''
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox
from app import db
from app.barrier import BarrierService
from app.ui.barrier_control_dialog import BarrierControlDialog
app = QApplication([])
db.init_db()
db.ensure_standard_site_drivers()
db.set_setting("arma_barrier_ip", "192.0.2.7")
db.set_setting("arma_barrier_trigger_hex", "0200030203")
db.set_setting("arma_barrier_trigger_verified", "1")
device = db.get_device_by_gate("CIKIS-1", "BARRIER")
db.update_device(device["id"], protocol="MOCK", ip="", port="5000")
service = BarrierService()
with patch.object(BarrierControlDialog, "_refresh_status", return_value=None), \
     patch.object(BarrierControlDialog, "_run_async", return_value=None), \
     patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
     patch("app.barrier.socket.create_connection", side_effect=AssertionError("No live TCP")), \
     patch("app.barrier.urllib.request.urlopen", side_effect=AssertionError("No live HTTP")):
    panel = BarrierControlDialog(service, "admin")
    assert panel.proto_combo.currentText() == "MOCK"
    assert panel.close_btn.isEnabled()
    panel._save_arma_profile()
    assert panel.proto_combo.currentText() == "METCOM_IO"
    assert panel.ip_input.text() == "192.0.2.7"
    assert panel.port_input.value() == 8080
    assert not panel.close_btn.isEnabled()
    assert "Tanımlı Değil" in panel.close_btn.text()
    panel._test_connection()  # This saves the lower fields before testing.
    assert db.get_device_by_gate("CIKIS-1", "BARRIER")["protocol"] == "METCOM_IO"
    panel.close()
    db.init_db()
    reopened = BarrierControlDialog(BarrierService(), "admin")
    assert reopened.proto_combo.currentText() == "METCOM_IO"
    assert reopened.arma_trigger_hex.text() == "0200030203"
    assert reopened.arma_trigger_verified.isChecked()
    assert not reopened.close_btn.isEnabled()
    reopened.close()
print("saved-profile-reopen-ok")
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", OTOPARK_DATA_DIR=str(tmp_path / "ui-data"))
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "saved-profile-reopen-ok" in result.stdout
