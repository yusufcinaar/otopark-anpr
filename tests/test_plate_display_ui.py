"""Bosluklu plaka sunumu eski kayitlari ve secilen kaydin kimligini korur."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROBE = r'''
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

data_dir = Path(os.environ['OTOPARK_DATA_DIR'])
data_dir.mkdir(parents=True, exist_ok=True)
(data_dir / 'otopark.db').touch()

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QMessageBox, QWidget
from app import db

app = QApplication([])
db.init_db()
case = sys.argv[1]
shown = '34 GGG 01'
stored = '34GGG01'

# Bu testler canli kamera, bariyer veya e-posta baglantisi kuramaz.
with patch('socket.socket.connect', side_effect=AssertionError('Network not allowed')), \
     patch('socket.socket.sendto', side_effect=AssertionError('Network not allowed')):
    if case == 'camera_history_payment':
        from app.ui.widgets import CameraPanel, DetectionHistoryPanel
        from app.ui.payment_dialog import PaymentDialog
        panel = CameraPanel('Cikis Kamerasi', 'CIKIS-1', is_exit=True)
        panel.set_last_plate(stored, 12)
        assert panel.plate_lbl.text() == shown
        assert panel.speed_lbl.text() == 'HIZ 12 km/sa'
        panel.set_last_plate('— — — —')
        assert panel.plate_lbl.text() == '— — — —'
        history = DetectionHistoryPanel()
        history.add_entry(stored, 'CIKIS-1', '12:30:00', 'Cikis')
        row = history.itemWidget(history.item(0))
        assert shown in [label.text() for label in row.findChildren(QLabel)]
        session = {'id': 1, 'plate': stored, 'fee_dec': '25.00', 'extra_fee_dec': '0.00'}
        payment = PaymentDialog(session)
        assert shown in [label.text() for label in payment.findChildren(QLabel)]
        payment._timer.stop()
        payment._confirm()
        assert payment.result_data['method'] == 'NAKIT'
        assert str(payment.result_data['cash_received']) == '25.00'
        assert session['plate'] == stored
        payment.close()
        panel.close()
        history.close()
    elif case == 'report_search':
        from app.ui.reports_dialog import ReportsDialog
        session_id = db.create_entry(stored, 'GIRIS-1')
        db.update_session_fields(session_id, {'status': 'ICERIDE'})
        db.create_entry('06ABC123', 'GIRIS-2')
        reports = ReportsDialog('kasiyer')
        reports.plate_input.setText(shown)
        reports._search()
        assert reports.table.rowCount() == 1
        assert reports.table.item(0, 0).text() == shown
        reports._show_detail(0, 0)
        assert reports.detail_text.text().startswith(shown + '   |')
        assert reports._results[0]['id'] == session_id
        assert reports._results[0]['plate'] == stored
        assert db.get_session(session_id)['plate'] == stored
        reports.close()
    elif case in ('subscriber_delete', 'blacklist_delete'):
        if case == 'subscriber_delete':
            from app.ui.subscribers_dialog import SubscribersDialog
            db.add_subscriber(stored, 'Ali Veli', '', None)
            dialog = SubscribersDialog()
            entries = db.list_subscribers
            action = 'ABONE_SILINDI'
        else:
            from app.ui.blacklist_dialog import BlacklistDialog
            db.add_blacklist(stored, 'Kontrol')
            dialog = BlacklistDialog()
            entries = db.list_blacklist
            action = 'KARA_LISTE_SILINDI'
        record = entries()[0]
        assert record['plate'] == stored
        assert dialog.table.item(0, 1).text() == shown
        assert int(dialog.table.item(0, 0).text()) == record['id']
        dialog.table.selectRow(0)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes) as question:
            dialog.remove_selected()
        assert shown in question.call_args.args[2]
        assert entries() == []
        audit = next(row for row in db.list_audit_logs() if row['action'] == action)
        assert audit['target'] == stored
        dialog.close()
    elif case == 'audit_search':
        from app.ui.audit_dialog import AuditLogDialog
        db.audit('kasiyer', 'ABONE_EKLENDI_GUNCELLENDI', target=stored, new_value='Ali Veli | Suresiz')
        audit = AuditLogDialog()
        for search in (shown, stored):
            audit.filter_input.setText(search)
            assert audit.table.rowCount() == 1
            assert audit.table.item(0, 3).text() == shown
            assert audit.table.item(0, 5).text() == 'Ali Veli | Suresiz'
        assert db.list_audit_logs()[0]['target'] == stored
        audit.close()
    elif case == 'manual_match':
        from app.ui.main_window import MainWindow
        host = QWidget()
        host.current_user = {'username': 'kasiyer', 'role': 'kasiyer'}
        host.service = MagicMock()
        host._handle_ingest_result = MagicMock()
        event = {'raw_plate': '34GGG02'}
        result = {
            'plate': event['raw_plate'], 'suggestions': [(stored, 0.9)],
            'inside': [{'id': 73, 'plate': stored, 'entry_time': '2026-09-19T10:00:00', 'entry_lane': 'GIRIS-1'}],
            'event': event,
        }
        def accept_matching(dialog):
            combo = dialog.findChild(QComboBox)
            assert combo.itemText(0).startswith(shown + '  (giris:')
            assert combo.itemData(0) == 73
            assert any('34 GGG 02' in label.text() for label in dialog.findChildren(QLabel))
            return QDialog.DialogCode.Accepted
        with patch('app.ui.main_window.QDialog.exec', accept_matching), \
             patch('app.ui.main_window.QInputDialog.getText', return_value=('Kamera okumasi duzeltildi', True)):
            MainWindow._handle_no_match_exit(host, result, event)
        host.service.manual_match_exit.assert_called_once_with(
            event, 73, host.current_user, 'Kamera okumasi duzeltildi')
        host.close()
    else:
        raise AssertionError(case)
print('OK', case)
'''


@pytest.mark.parametrize('case', [
    'camera_history_payment', 'report_search', 'subscriber_delete',
    'blacklist_delete', 'audit_search', 'manual_match',
])
def test_plate_display_ui(case, tmp_path):
    result = subprocess.run(
        [sys.executable, '-c', _PROBE, case],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                 OTOPARK_DATA_DIR=str(tmp_path / 'plate-ui')),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'OK {case}' in result.stdout
