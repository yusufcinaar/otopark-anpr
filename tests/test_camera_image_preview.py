"""Full camera/capture images and saved history photos open without hardware."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROBE = r'''
import os, sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

data = Path(os.environ['OTOPARK_DATA_DIR'])
data.mkdir(parents=True, exist_ok=True)
(data / 'otopark.db').touch()
from app.ui.widgets import CameraPanel, DetectionHistoryPanel
from app.ui.image_viewer import ImagePreviewLabel
from app.ui.main_window import MainWindow
from app.ui.barrier_shortcut import ExitBarrierShortcut

app = QApplication([])
case = sys.argv[1]
with patch('socket.socket.connect', side_effect=AssertionError('No network')), \
     patch('socket.socket.sendto', side_effect=AssertionError('No network')):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[:, :] = (0, 0, 255)
    window = QMainWindow()
    if case.startswith('camera'):
        exit_camera = case == 'camera_exit'
        panel = CameraPanel('Cikis' if exit_camera else 'Giris',
                            'CIKIS-1' if exit_camera else 'GIRIS-1', is_exit=exit_camera)
        window.setCentralWidget(panel)
        window.resize(700, 430)
        window.show()
        barriers = MagicMock()
        barriers.manual_open.return_value = True
        shortcut = ExitBarrierShortcut(window, barriers,
            lambda: {'username': 'cashier', 'role': 'kasiyer'}, lambda text: None)
        panel.set_frame(frame)
        panel.set_last_plate('34GGG01')
        panel.set_last_capture(frame)
        app.processEvents()
        for label in (panel.image_lbl, panel.last_capture_lbl):
            assert label.source_pixmap.size().width() == 1920
            assert label.pixmap().width() < 1920
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert label._viewer.isVisible()
            assert label._viewer.source_pixmap.size().width() == 1920
        live_view = panel.image_lbl._viewer
        frame[:, :] = (255, 0, 0)
        panel.set_frame(frame)
        assert live_view.source_pixmap.toImage().pixelColor(0, 0).name() == '#ff0000'
        assert panel.image_lbl.source_pixmap.toImage().pixelColor(0, 0).name() == '#0000ff'
        # Camera updates and the snapshot window do not block the cashier's F4.
        live_view.raise_()
        live_view.activateWindow()
        live_view.view.setFocus()
        app.processEvents()
        QTest.keyClick(live_view.view, Qt.Key.Key_F4)
        until = time.monotonic() + 4
        while (not barriers.manual_open.called or shortcut.busy) and time.monotonic() < until:
            app.processEvents()
            QTest.qWait(5)
        barriers.manual_open.assert_called_once()
        assert not shortcut.busy
        shortcut.disable()
        panel.image_lbl.close_viewer()
        panel.last_capture_lbl.close_viewer()
    elif case == 'history':
        path = data / 'saved-capture.png'
        source = QPixmap(1920, 1080)
        source.fill(Qt.GlobalColor.red)
        assert source.save(str(path))
        history = DetectionHistoryPanel()
        window.setCentralWidget(history)
        window.show()
        history.add_entry('34GGG01', 'GIRIS-1', '12:00', thumbnail=source, image_path=str(path))
        row = history.itemWidget(history.item(0))
        label = row.findChild(ImagePreviewLabel)
        assert label._source_pixmap.width() <= 46, 'History retains only a small thumbnail'
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert label._viewer.source_pixmap.width() == 1920
        assert '34 GGG 01' in label._viewer.windowTitle()
        label.close_viewer()
        path.unlink()
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert label._viewer is None
        assert not label.has_image()
    elif case == 'missing_event_image':
        panel = CameraPanel('Giris', 'GIRIS-1')
        window.setCentralWidget(panel)
        panel.set_last_capture(frame)
        assert panel.last_capture_lbl.has_image()
        host = SimpleNamespace(
            _last_capture_path_for_lane={'GIRIS-1': 'old.jpg'},
            _last_frame_for_lane={}, _prepare_event_images=lambda event: None,
            _set_health=lambda *args: None, pts_health=None,
            _panel_for_gate=lambda gate: panel, service=MagicMock(),
            _handle_ingest_result=MagicMock(),
        )
        MainWindow.process_camera_event(host, {'gate_id': 'GIRIS-1', 'raw_plate': '34GGG02'})
        assert not panel.last_capture_lbl.has_image(), 'New car must not open previous car photo'
        assert 'GIRIS-1' not in host._last_capture_path_for_lane
        host.service.ingest_camera_event.assert_called_once()
    window.close()
    app.processEvents()
print('PASS ' + case)
'''


@pytest.mark.parametrize('case', [
    'camera_entry', 'camera_exit', 'history', 'missing_event_image',
])
def test_camera_images_open_originals(case, tmp_path):
    result = subprocess.run(
        [sys.executable, '-c', _PROBE, case],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                 OTOPARK_DATA_DIR=str(tmp_path / 'image-ui')),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=35,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + case in result.stdout
