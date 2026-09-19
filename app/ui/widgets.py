"""Kasiyer arayuzunde kullanilan modern widget'lar."""
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QGraphicsDropShadowEffect, QSizePolicy,
)
from PySide6.QtGui import QColor

from app.ui.theme import COLORS
from app.ui.image_viewer import ImagePreviewLabel
from app.services.plate_utils import format_plate


def numpy_to_pixmap(frame: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def add_card_shadow(widget: QWidget):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(24)
    shadow.setOffset(0, 4)
    shadow.setColor(QColor(0, 0, 0, 140))
    widget.setGraphicsEffect(shadow)


class CameraPanel(QFrame):
    """Bir kamerayi (giris veya cikis) temsil eden modern kart."""
    def __init__(self, title: str, badge: str, is_exit: bool = False, parent=None):
        super().__init__(parent)
        self.is_exit = is_exit
        self._camera_title = title
        self.setObjectName("cameraCard")
        add_card_shadow(self)

        from app.config import SIMULATION_MODE
        self.simulation = SIMULATION_MODE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        header.addWidget(title_lbl)
        header.addStretch()
        badge_lbl = QLabel(badge)
        badge_lbl.setObjectName("laneBadge")
        header.addWidget(badge_lbl)
        layout.addLayout(header)

        if self.simulation:
            placeholder = "SIMULASYON MODU\n\nGercek kamera baglanmadi"
        else:
            placeholder = "KAMERA BEKLENIYOR\n\nWebhook / RTSP baglantisi yok"
        feed_row = QHBoxLayout()
        feed_row.setSpacing(8)

        self.image_lbl = ImagePreviewLabel(placeholder, title=f"{title} — Anlık görüntü")
        self.image_lbl.setObjectName("cameraFeed")
        self.image_lbl.setMinimumSize(300, 105)
        self.image_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        feed_row.addWidget(self.image_lbl, 2)

        self.last_capture_lbl = ImagePreviewLabel(
            "SON OKUMA\n\nHenuz arac okunmadi", title=f"{title} — Son okuma")
        self.last_capture_lbl.setObjectName("cameraFeed")
        self.last_capture_lbl.setMinimumSize(145, 105)
        self.last_capture_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.last_capture_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.last_capture_lbl.setStyleSheet("border: 1px solid #aebed1; color: #61758f;")
        feed_row.addWidget(self.last_capture_lbl, 1)
        layout.addLayout(feed_row, 1)

        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(8)
        self.plate_lbl = QLabel("— — — —")
        self.plate_lbl.setObjectName("plateBadge")
        self.plate_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_row.addWidget(self.plate_lbl, 1)
        self.time_lbl = QLabel("--:--:--")
        self.time_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        overlay_row.addWidget(self.time_lbl)
        self.speed_lbl = QLabel("HIZ — km/sa")
        self.speed_lbl.setObjectName("speedBadge")
        overlay_row.addWidget(self.speed_lbl)
        layout.addLayout(overlay_row)

        controls = QHBoxLayout()
        self.plate_combo = None
        if is_exit:
            self.action_btn = QPushButton("Cikisi Simule Et")
        else:
            self.action_btn = QPushButton("Girisi Simule Et")
        self.action_btn.setObjectName("primaryBtn")
        # Production modunda simule butonu gizli; olaylar webhook/RTSP ile gelir
        self.action_btn.setVisible(self.simulation)
        if not self.simulation:
            self.webhook_lbl = QLabel("Webhook bekleniyor...")
            self.webhook_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; padding: 4px;")
            controls.addWidget(self.webhook_lbl, 1)
        controls.addWidget(self.action_btn)
        layout.addLayout(controls)

    def set_frame(self, frame: np.ndarray):
        self.image_lbl.set_image(numpy_to_pixmap(frame))

    def set_last_capture(self, frame: np.ndarray):
        """Plakanin okundugu tam arac karesini yeni okuma gelene kadar tutar."""
        self.last_capture_lbl.set_image(
            numpy_to_pixmap(frame),
            title=f"{self._camera_title} — Son okuma | {self.plate_lbl.text()} | {self.time_lbl.text()}")

    def set_last_plate(self, text: str, speed_kmh=None):
        from datetime import datetime
        self.plate_lbl.setText(format_plate(text))
        self.time_lbl.setText(datetime.now().strftime("%H:%M:%S"))
        try:
            self.speed_lbl.setText(f"HIZ {float(speed_kmh):.0f} km/sa" if speed_kmh not in (None, "") else "HIZ — km/sa")
        except (TypeError, ValueError):
            self.speed_lbl.setText("HIZ — km/sa")

    def set_online(self, online: bool):
        if not hasattr(self, "webhook_lbl"):
            return
        color = COLORS["success"] if online else COLORS["danger"]
        self.webhook_lbl.setText("PTS aktif" if online else "Kamera baglantisi yok")
        self.webhook_lbl.setStyleSheet(f"color: {color}; font-size: 11px; padding: 4px; font-weight: 600;")


_KIND_COLORS = {
    "giris": "accent_hover",
    "cikis": "warning",
    "odeme": "success",
    "uyari": "danger",
    "manuel": "text_muted",
    "bilgi": "text",
}


def classify_event(text: str) -> str:
    if "GIRIS kaydedildi" in text:
        return "giris"
    if "CIKIS talebi" in text:
        return "cikis"
    if "ODEME ALINDI" in text:
        return "odeme"
    if "MANUEL INCELEME" in text or "UYARI" in text:
        return "uyari"
    return "bilgi"


class DetectionHistoryPanel(QListWidget):
    """Sol taraftaki, kucuk resimli (thumbnail) hareket/tespit gecmisi listesi.

    Referans: ticari plaka tanima yazilimlarindaki (ArmaKontrol vb.) tespit
    kayit paneli - her satirda arac gorseli + plaka + kamera/zaman + aciklama.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logList")
        self.setSpacing(3)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def add_entry(self, plate_display: str, lane: str, time_str: str,
                  description: str = "", kind: str = "bilgi", thumbnail: QPixmap = None,
                  image_path: str = ""):
        scrollbar = self.verticalScrollBar()
        was_at_top = scrollbar.value() <= scrollbar.minimum() + 2
        previous_position = scrollbar.value()
        color = COLORS[_KIND_COLORS.get(kind, "text")]

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(6, 6, 6, 6)
        row_layout.setSpacing(10)

        thumb = ImagePreviewLabel(title=f"{format_plate(plate_display)} | {lane} | {time_str}")
        thumb.setFixedSize(46, 46)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet(
            f"background-color: {COLORS['surface_alt']}; border-radius: 6px; "
            f"color: {COLORS['text_muted']}; font-size: 9px; font-weight: 600;"
        )
        if image_path:
            # Listede sadece kucuk onizleme ve dosya yolu tutulur. Tam fotograf
            # ancak tiklandiginda yuklenir; 150 buyuk kare bellekte birikmez.
            preview = thumbnail.scaled(
                46, 46, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ) if thumbnail is not None and not thumbnail.isNull() else None
            thumb.set_image_path(image_path, preview=preview)
        elif thumbnail is not None and not thumbnail.isNull():
            # Kayit dosyasi olmayan genel durum satirinin kucuk resmi.
            thumb.setPixmap(thumbnail.scaled(
                46, 46, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            thumb.setText("ARAC")
        row_layout.addWidget(thumb)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        plate_lbl = QLabel(format_plate(plate_display))
        plate_lbl.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 13px; font-family: Consolas;")
        text_col.addWidget(plate_lbl)
        meta_lbl = QLabel(f"{lane}  ·  {time_str}")
        meta_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px;")
        text_col.addWidget(meta_lbl)
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
            desc_lbl.setWordWrap(True)
            text_col.addWidget(desc_lbl)
        row_layout.addLayout(text_col, 1)

        item = QListWidgetItem()
        self.insertItem(0, item)
        self.setItemWidget(item, row)
        item.setSizeHint(row.sizeHint())

        # Yeni olay en uste eklenir. Kullanici listenin basindaysa yeni araci
        # goster; eski kayitlari inceliyorsa goruntuyu zorla baska satira atma.
        added_height = max(1, item.sizeHint().height() + self.spacing())
        if was_at_top:
            QTimer.singleShot(0, lambda bar=scrollbar: bar.setValue(bar.minimum()))
        else:
            QTimer.singleShot(
                0, lambda bar=scrollbar, pos=previous_position + added_height:
                bar.setValue(min(pos, bar.maximum())))

        while self.count() > 150:
            self.takeItem(self.count() - 1)
