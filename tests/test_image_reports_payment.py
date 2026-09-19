"""Report/payment photo inspection uses full images and preserves payment flow."""
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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])
case = sys.argv[1]
directory = Path(os.environ['OTOPARK_DATA_DIR'])
directory.mkdir(parents=True, exist_ok=True)

def photo(name, color, width, height):
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(color))
    path = directory / name
    assert pixmap.save(str(path), 'PNG')
    return str(path)

red = photo('entry-a.png', 'red', 1920, 1080)
blue = photo('exit-a.png', 'blue', 2048, 1536)
green = photo('entry-b.png', 'green', 1600, 1200)
invalid = directory / 'broken.png'
invalid.write_bytes(b'not an image')
missing = str(directory / 'missing.png')

records = [
    {'id': 1, 'plate': '34GGG01', 'entry_time': '2026-09-19 10:11:12',
     'exit_time': '2026-09-19 12:13:14', 'entry_image': red, 'exit_image': blue,
     'status': 'TAMAMLANDI', 'fee_dec': '25.00', 'extra_fee_dec': '0.00'},
    {'id': 2, 'plate': '06ABC123', 'entry_time': '2026-09-19 14:15:16',
     'entry_image': green, 'exit_image': missing, 'status': 'ICERIDE'},
    {'id': 3, 'plate': '35DEF456', 'entry_image': str(invalid),
     'exit_image': '', 'status': 'ICERIDE'},
]

def click(label):
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    app.processEvents()

def check_source(widget, color, width, height):
    source = widget.source_pixmap
    assert (source.width(), source.height()) == (width, height)
    assert source.toImage().pixelColor(0, 0) == QColor(color)

# Photo review must never connect to actual cameras, barriers or mail servers.
with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), \
     patch('socket.socket.sendto', side_effect=AssertionError('Network forbidden')):
    if case.startswith('reports'):
        from app.ui.reports_dialog import ReportsDialog
        with patch('app.ui.reports_dialog.db.search_sessions', return_value=records), \
             patch('app.ui.reports_dialog.db.audit'):
            dialog = ReportsDialog('kasiyer')
            dialog.show()
            app.processEvents()
            dialog._show_detail(0, 0)
            app.processEvents()
            click(dialog.entry_image)
            first = dialog.entry_image._viewer
            assert first is not None and first.isVisible()
            check_source(first, 'red', 1920, 1080)
            assert '34 GGG 01' in first.windowTitle()
            assert 'Giriş' in first.windowTitle()
            assert '2026-09-19 10:11:12' in first.windowTitle()
            assert dialog.entry_image.pixmap().width() < first.source_pixmap.width()
            if case == 'reports_entry_exit':
                click(dialog.exit_image)
                exit_view = dialog.exit_image._viewer
                assert exit_view is not None and exit_view.isVisible()
                check_source(exit_view, 'blue', 2048, 1536)
                assert 'Çıkış' in exit_view.windowTitle()
                assert '2026-09-19 12:13:14' in exit_view.windowTitle()
                dialog.reject()
                assert dialog.entry_image._viewer is None
                assert dialog.exit_image._viewer is None
            elif case == 'reports_switch_and_missing':
                dialog._show_detail(1, 0)
                app.processEvents()
                check_source(dialog.entry_image, 'green', 1600, 1200)
                check_source(first, 'red', 1920, 1080)
                assert '34 GGG 01' in first.windowTitle(), 'Selection changed open snapshot'
                assert not dialog.exit_image.has_image()
                click(dialog.exit_image)
                assert dialog.exit_image._viewer is None
                click(dialog.entry_image)
                current = dialog.entry_image._viewer
                check_source(current, 'green', 1600, 1200)
                assert '06 ABC 123' in current.windowTitle()
                assert '2026-09-19 14:15:16' in current.windowTitle()
                dialog._show_detail(2, 0)
                assert not dialog.entry_image.has_image()
                assert not dialog.exit_image.has_image()
                check_source(current, 'green', 1600, 1200)
                dialog.entry_image.close_viewer()
                click(dialog.entry_image)
                assert dialog.entry_image._viewer is None, 'Broken image retained old clickable photo'
                dialog._show_detail(0, 0)
                click(dialog.entry_image)
                search_snapshot = dialog.entry_image._viewer
                dialog._search()
                assert not dialog.entry_image.has_image()
                assert not dialog.exit_image.has_image()
                check_source(search_snapshot, 'red', 1920, 1080)
                dialog.close()
                assert dialog.entry_image._viewer is None
            else:
                raise AssertionError(case)
    else:
        from app.ui.payment_dialog import PaymentDialog
        dialog = PaymentDialog(dict(records[0]))
        dialog.show()
        app.processEvents()
        exit_label, entry_label = dialog._image_labels
        if case == 'payment_pause_resume':
            dialog._seconds_left = 2
            click(entry_label)
            check_source(entry_label._viewer, 'red', 1920, 1080)
            assert '34 GGG 01' in entry_label._viewer.windowTitle()
            assert 'Giriş' in entry_label._viewer.windowTitle()
            click(exit_label)
            check_source(exit_label._viewer, 'blue', 2048, 1536)
            assert 'Çıkış' in exit_label._viewer.windowTitle()
            assert len(dialog._open_image_labels) == 2
            assert not dialog._timer.isActive()
            # Even a timeout already queued when the photo opened is harmless.
            dialog._tick_timeout()
            QTest.qWait(1150)
            assert dialog._seconds_left == 2
            assert dialog.isVisible() and dialog.result_data is None
            entry_label.close_viewer()
            assert not dialog._timer.isActive()
            assert len(dialog._open_image_labels) == 1
            # Close the second photo through an actual user input.
            QTest.keyClick(exit_label._viewer, Qt.Key.Key_Escape)
            app.processEvents()
            assert not dialog._open_image_labels
            assert dialog._timer.isActive()
            assert dialog._seconds_left == 2, 'Resuming prematurely consumed a second'
            QTest.qWait(1150)
            assert dialog._seconds_left == 1
            assert dialog.result_data is None
            dialog.close()
            assert not dialog._timer.isActive()
        elif case == 'payment_no_timeout_during_inspection':
            dialog._seconds_left = 0
            click(entry_label)
            dialog._tick_timeout()
            assert dialog.isVisible()
            assert dialog.result_data is None
            assert dialog._seconds_left == 0
            dialog.close()
            assert not dialog._timer.isActive()
            assert entry_label._viewer is None
            assert not dialog._open_image_labels
            dialog._tick_timeout()
            assert not dialog.isVisible() and dialog.result_data is None
        elif case == 'payment_done_cleanup':
            for method in ('reject', 'close', '_confirm'):
                if method != 'reject':
                    dialog = PaymentDialog(dict(records[0]))
                    dialog.show()
                    app.processEvents()
                for label in dialog._image_labels:
                    click(label)
                assert len(dialog._open_image_labels) == 2
                getattr(dialog, method)()
                assert all(label._viewer is None for label in dialog._image_labels)
                assert not dialog._open_image_labels
                assert not dialog._timer.isActive()
                assert dialog._closing and not dialog.isVisible()
                if method == '_confirm':
                    assert dialog.result() == QDialog.DialogCode.Accepted
                    assert dialog.result_data['method'] == 'NAKIT'
                    assert str(dialog.result_data['cash_received']) == '25.00'
                else:
                    assert dialog.result_data is None
        else:
            raise AssertionError(case)

app.processEvents()
print('OK', case)
'''


@pytest.mark.parametrize('case', [
    'reports_entry_exit', 'reports_switch_and_missing', 'payment_pause_resume',
    'payment_no_timeout_during_inspection', 'payment_done_cleanup',
])
def test_report_payment_image_review(case, tmp_path):
    result = subprocess.run(
        [sys.executable, '-c', _PROBE, case],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                 OTOPARK_DATA_DIR=str(tmp_path / 'image-review')),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'OK {case}' in result.stdout
