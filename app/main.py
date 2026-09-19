import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from app import db
from app.config import DATA_DIR
from app.ui.main_window import MainWindow
from app.ui.login_dialog import LoginDialog
from app.ui.theme import STYLESHEET


def main():
    db.init_db()
    db.ensure_standard_site_drivers()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # Ayni bilgisayarda ikinci kopya webhook portunu doldurur ve kamera
    # olaylarinin yanlis pencereye gitmesine neden olur. Tek instance calistir.
    lock = QLockFile(f"{DATA_DIR}/otopark-anpr.lock")
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None, "Program Zaten Acik",
            "Otopark ANPR zaten calisiyor. Acik pencereyi kullanin."
        )
        return

    login = LoginDialog()
    if login.exec() != LoginDialog.DialogCode.Accepted:
        sys.exit(0)

    window = MainWindow(current_user=login.current_user)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
