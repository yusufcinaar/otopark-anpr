"""Offscreen image-viewer regressions; no application or hardware is started."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROBE = r'''
import os
import sys
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from shiboken6 import delete
from app.ui.image_viewer import ImagePreviewLabel

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
case = sys.argv[1]
data_dir = Path(os.environ['OTOPARK_DATA_DIR'])
data_dir.mkdir(parents=True, exist_ok=True)

def pixmap(color='red', width=1920, height=1080):
    image = QPixmap(width, height)
    image.fill(QColor(color))
    return image

def drain():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

host = QWidget()
host.resize(400, 260)
layout = QVBoxLayout(host)
label = ImagePreviewLabel('Resim bekleniyor', title='Giris - 34 GGG 01')
layout.addWidget(label)
host.show()
events = []
label.image_opened.connect(lambda: events.append('open'))
label.image_closed.connect(lambda: events.append('close'))
drain()

with patch('socket.socket.connect', side_effect=AssertionError('No network')):
    if case == 'original_resize_click_keyboard':
        original = pixmap()
        label.set_image(original)
        assert label.has_image()
        assert label.source_pixmap.size() == original.size()
        assert label.pixmap().width() < original.width()
        assert label.pixmap().width() <= label.contentsRect().width()
        before = label.pixmap().size()
        host.resize(700, 440)
        drain()
        assert label.pixmap().width() > before.width()
        assert abs(label.pixmap().width() / label.pixmap().height() - 16/9) < .01
        assert host.width() == 700
        assert label.sizeHint().width() < original.width()
        assert label.minimumSizeHint().width() == 0
        QTest.mouseClick(label, Qt.MouseButton.RightButton)
        assert label._viewer is None
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        drain()
        viewer = label._viewer
        assert viewer.isVisible()
        assert viewer.source_pixmap.size() == original.size()
        assert viewer.windowTitle() == 'Giris - 34 GGG 01'
        assert viewer.parentWidget() is host
        assert not viewer.isModal()
        assert events == ['open']
        QTest.keyClick(viewer, Qt.Key.Key_Escape)
        drain()
        assert label._viewer is None
        assert events == ['open', 'close']
        assert host.isVisible()
        QTest.keyClick(label, Qt.Key.Key_Return)
        assert label._viewer is not None
        assert events == ['open', 'close', 'open']
    elif case == 'zoom_fit_wheel_bounds':
        label.set_image(pixmap())
        label.show_image()
        drain()
        viewer = label._viewer
        canvas = viewer.view
        initial = canvas.zoom_factor
        assert 0 < initial < 1
        viewer.zoom_in_btn.click()
        assert canvas.zoom_factor > initial
        viewer.actual_size_btn.click()
        assert canvas.zoom_factor == 1
        assert viewer.zoom_label.text() == '%100'
        wheel = QWheelEvent(QPointF(60, 60), QPointF(60, 60), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(canvas.viewport(), wheel)
        assert canvas.zoom_factor > 1
        for _ in range(100):
            canvas.zoom(1.25)
        assert canvas.zoom_factor == canvas.MAX_SCALE
        assert not viewer.zoom_in_btn.isEnabled()
        for _ in range(150):
            canvas.zoom(.8)
        assert canvas.zoom_factor == canvas.MIN_SCALE
        assert not viewer.zoom_out_btn.isEnabled()
        viewer.fit_btn.click()
        drain()
        assert .01 < canvas.zoom_factor < 1
        old_scale = canvas.zoom_factor
        viewer.resize(700, 500)
        drain()
        assert canvas.zoom_factor < old_scale
        assert label.source_pixmap.size() == pixmap().size()
    elif case == 'snapshot_lifetime':
        first = pixmap('red')
        label.set_image(first)
        label.show_image()
        viewer = label._viewer
        source_copy = label.source_pixmap
        source_copy.fill(QColor('blue'))
        assert label.source_pixmap.toImage().pixelColor(0, 0) == QColor('red')
        label.set_image(pixmap('green', 1280, 720))
        assert viewer.source_pixmap.toImage().pixelColor(0, 0) == QColor('red')
        assert viewer.source_pixmap.size() == first.size()
        viewer_copy = viewer.source_pixmap
        viewer_copy.fill(QColor('blue'))
        assert viewer.source_pixmap.toImage().pixelColor(0, 0) == QColor('red')
        label.show_image()
        assert label._viewer is viewer
        assert viewer.source_pixmap.toImage().pixelColor(0, 0) == QColor('green')
        assert events == ['open']
        label.close_viewer()
        assert events == ['open', 'close']
        # Open again before deferred deletion of the old viewer is delivered.
        label.show_image()
        second = label._viewer
        drain()
        assert second is label._viewer
        assert events == ['open', 'close', 'open']
        assert second.source_pixmap.toImage().pixelColor(0, 0) == QColor('green')
        label.close_viewer()
        drain()
        assert events == ['open', 'close', 'open', 'close']
        label.close_viewer()
        assert events == ['open', 'close', 'open', 'close']
    elif case == 'missing_corrupt_clear':
        path = data_dir / 'valid.png'
        assert pixmap().save(str(path))
        label.set_image_path(path)
        assert label.has_image()
        label.set_image_path(data_dir / 'missing.png')
        assert not label.has_image()
        assert label.pixmap().isNull()
        assert label.text() == 'Görsel bulunamadı'
        label.show_image()
        assert label._viewer is None
        label.set_image_path(path)
        corrupt = data_dir / 'corrupt.png'
        corrupt.write_bytes(b'not a png')
        label.set_image_path(corrupt)
        assert not label.has_image()
        assert label.source_pixmap.isNull()
        label.set_image(pixmap())
        label.set_image(QPixmap())
        assert not label.has_image()
        assert events == []
    elif case == 'lazy_history_original_and_deleted_file':
        original = pixmap('blue')
        path = data_dir / 'capture.png'
        assert original.save(str(path))
        tiny = original.scaled(46, 46, Qt.AspectRatioMode.KeepAspectRatio)
        label.set_image_path(path, preview=tiny)
        assert label._source_pixmap.width() == 46
        assert label.source_pixmap.size() == original.size()
        label.show_image()
        assert label._viewer.source_pixmap.size() == original.size()
        label.close_viewer()
        drain()
        path.unlink()
        label.show_image()
        assert label._viewer is None
        assert not label.has_image()
        assert label.source_pixmap.isNull()
        corrupt = data_dir / 'corrupt.png'
        corrupt.write_bytes(b'not a png')
        label.set_image_path(corrupt, preview=tiny)
        label.show_image()
        assert label._viewer is None
        assert not label.has_image()
    elif case == 'viewer_destroyed_signal':
        label.set_image(pixmap())
        label.show_image()
        assert events == ['open']
        delete(label._viewer)
        drain()
        assert label._viewer is None
        assert events == ['open', 'close']
        label.show_image()
        assert events == ['open', 'close', 'open']
    else:
        raise AssertionError(case)

label.close_viewer()
host.close()
drain()
print('OK', case)
'''


@pytest.mark.parametrize('case', [
    'original_resize_click_keyboard',
    'zoom_fit_wheel_bounds',
    'snapshot_lifetime',
    'missing_corrupt_clear',
    'lazy_history_original_and_deleted_file',
    'viewer_destroyed_signal',
])
def test_image_viewer_offscreen(case, tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
               OTOPARK_DATA_DIR=str(tmp_path / 'probe-data'))
    result = subprocess.run(
        [sys.executable, '-c', _PROBE, case],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert f'OK {case}' in result.stdout
