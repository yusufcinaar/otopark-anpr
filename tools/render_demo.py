"""Render real Qt screens using temporary, synthetic data only.

Run: python tools/render_demo.py
No camera, webhook listener, SMTP service or physical barrier is started.
"""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
demo_data = tempfile.TemporaryDirectory(prefix='anpr-synthetic-')
os.environ['OTOPARK_DATA_DIR'] = demo_data.name
os.environ['SIMULATION_MODE'] = 'true'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from app import db
from app.ui.main_window import MainWindow
from app.ui.reports_dialog import ReportsDialog
from app.ui.theme import STYLESHEET
from app.ui.widgets import numpy_to_pixmap
from app.sim_utils import render_plate_image


def scene(plate, color):
    frame = np.full((540, 960, 3), (38, 30, 23), dtype=np.uint8)
    cv2.rectangle(frame, (0, 320), (960, 540), (63, 54, 45), -1)
    cv2.line(frame, (100, 540), (315, 260), (95, 90, 79), 5)
    cv2.line(frame, (860, 540), (645, 260), (95, 90, 79), 5)
    cv2.rectangle(frame, (277, 250), (683, 425), color, -1)
    roof = np.array([[330, 165], [630, 165], [683, 260], [277, 260]])
    cv2.fillPoly(frame, [roof], color)
    cv2.rectangle(frame, (342, 185), (618, 248), (63, 54, 37), -1)
    cv2.rectangle(frame, (286, 280), (364, 310), (190, 229, 250), -1)
    cv2.rectangle(frame, (596, 280), (674, 310), (190, 229, 250), -1)
    cv2.rectangle(frame, (297, 408), (340, 449), (18, 18, 18), -1)
    cv2.rectangle(frame, (620, 408), (663, 449), (18, 18, 18), -1)
    plate_img = render_plate_image(plate)
    frame[340:405, 350:610] = cv2.resize(plate_img, (260, 65))
    cv2.putText(frame, 'DEMO / SENTETIK GORUNTU', (28, 42), cv2.FONT_HERSHEY_SIMPLEX, .75, (207, 204, 199), 2, cv2.LINE_AA)
    return frame


def main():
    app = QApplication([])
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    for name in ['segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf']:
        if (fonts/name).exists():
            QFontDatabase.addApplicationFont(str(fonts/name))
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(STYLESHEET)
    out = ROOT/'docs/images'
    out.mkdir(parents=True, exist_ok=True)
    plates = ['34ABC123', '06DEF456', '35XYZ789']
    frames = [scene(plate, color) for plate, color in zip(plates, [(166,140,98), (174,179,186), (89,104,160)])]
    with patch('socket.socket.connect', side_effect=AssertionError('Demo cannot access network')), \
         patch.object(MainWindow, '_start_configured_cameras'), \
         patch.object(MainWindow, '_start_webhook'), \
         patch.object(MainWindow, '_check_missed_reports_safely'), \
         patch('app.ui.main_window.start_scheduler', return_value=None):
        db.init_db()
        db.ensure_standard_site_drivers()
        for index, (plate, frame) in enumerate(zip(plates, frames)):
            path = str(Path(demo_data.name)/f'synthetic-{index}.png')
            cv2.imwrite(path, frame)
            sid = db.create_entry(plate, 'GIRIS-1' if index != 1 else 'GIRIS-2', path)
            fields = {'entry_time': f'2026-09-19 {9+index:02}:15:00', 'entry_speed_kmh': 12+index}
            if index == 2:
                fields.update(status='TAMAMLANDI', exit_time='2026-09-19 13:45:00', exit_lane='CIKIS-1', exit_image=path, duration_minutes=150, fee_dec='60.00', paid=1)
            db.update_session_fields(sid, fields)
        window = MainWindow({'username':'demo','role':'sistem_yoneticisi'})
        window.resize(1600, 980)
        window.show()
        app.processEvents()
        window._clock_timer.stop()
        window.clock_lbl.setText('19 Eylül 2026 • DEMO — Tüm kayıtlar sentetiktir')
        for panel, plate, frame in zip([window.entry1_panel,window.entry2_panel,window.exit_panel], plates, frames):
            panel.set_frame(frame)
            panel.set_last_capture(frame)
            panel.set_last_plate(plate, speed_kmh=14)
            panel.time_lbl.setText('13:45:00')
        window.history_panel.clear()
        for index in range(6):
            i=index%3
            window.history_panel.add_entry(plates[i], 'CIKIS-1' if i==2 else f'GIRIS-{i+1}', f'13:{40+index:02}:00', 'Örnek araç kaydı', 'cikis' if i==2 else 'giris', numpy_to_pixmap(frames[i]))
        window._set_health(window.webhook_health, 'WEBHOOK', 'DEMO', 'waiting')
        window._set_health(window.pts_health, 'PTS', 'SİMÜLASYON', 'waiting')
        window._set_health(window.barrier_health, 'BARIYER', 'SİMÜLASYON', 'ok')
        app.processEvents()
        assert window.grab().save(str(out/'overview.png'))
        report = ReportsDialog('demo')
        report.resize(1440, 900)
        report.show()
        report._search()
        row = next(i for i,s in enumerate(report._results) if s['plate']=='35XYZ789')
        report.table.selectRow(row)
        report._show_detail(row,0)
        app.processEvents()
        assert report.grab().save(str(out/'reports.png'))
        report.close()
        window.close()
        app.processEvents()
    demo_data.cleanup()
    print('Rendered overview.png and reports.png from synthetic data.')


if __name__ == '__main__':
    main()
