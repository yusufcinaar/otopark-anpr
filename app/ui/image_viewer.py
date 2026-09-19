"""Single-click previews with an independent, full-resolution image viewer.

Call ``set_image`` with the original pixmap, not an already scaled thumbnail.
The preview is fitted to its label while an open viewer keeps its own snapshot,
so incoming camera frames never replace the image being inspected.
"""
from pathlib import Path
import weakref

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)
from shiboken6 import isValid


class _ImageView(QGraphicsView):
    """An image canvas with bounded zoom and mouse-wheel navigation."""

    scale_changed = Signal(float)
    MIN_SCALE = 0.01
    MAX_SCALE = 16.0

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._fit_mode = True
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._image_item = QGraphicsPixmapItem(pixmap)
        self._scene.addItem(self._image_item)
        self._scene.setSceneRect(self._image_item.boundingRect())
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    @property
    def zoom_factor(self) -> float:
        return self.transform().m11()

    def fit_image(self):
        self._fit_mode = True
        rect = self._image_item.boundingRect()
        if rect.isEmpty():
            return
        viewport = self.viewport().size()
        # A small inset prevents scroll bars oscillating during resize/fit.
        factor = min(max(1, viewport.width() - 4) / rect.width(),
                     max(1, viewport.height() - 4) / rect.height())
        self._apply_scale(factor)
        self.centerOn(self._image_item)

    def set_image(self, pixmap: QPixmap):
        self._image_item.setPixmap(pixmap)
        self._scene.setSceneRect(self._image_item.boundingRect())
        self.fit_image()

    def _apply_scale(self, factor: float):
        factor = max(self.MIN_SCALE, min(self.MAX_SCALE, factor))
        self.setTransform(QTransform.fromScale(factor, factor))
        self.scale_changed.emit(factor)

    def zoom(self, multiplier: float):
        self._fit_mode = False
        self._apply_scale(self.zoom_factor * multiplier)

    def actual_size(self):
        self._fit_mode = False
        self._apply_scale(1.0)
        self.centerOn(self._image_item)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom(1.25 if delta > 0 else 0.8)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_image()


class ImageViewerDialog(QDialog):
    """Modeless viewer; ``source_pixmap`` returns a copy of its fixed snapshot."""

    def __init__(self, pixmap: QPixmap, title: str = "Görsel", parent=None):
        super().__init__(parent)
        self._source_pixmap = QPixmap(pixmap)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.resize(1100, 760)
        self.setMinimumSize(420, 300)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setToolTip("Uzaklaştır")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setToolTip("Yakınlaştır")
        self.fit_btn = QPushButton("Pencereye Sığdır")
        self.actual_size_btn = QPushButton("%100")
        self.zoom_label = QLabel()
        self.close_btn = QPushButton("Kapat (Esc)")
        for button in (self.zoom_out_btn, self.zoom_in_btn,
                       self.fit_btn, self.actual_size_btn):
            button.setAutoDefault(False)
            toolbar.addWidget(button)
        toolbar.addWidget(self.zoom_label)
        toolbar.addStretch()
        self.close_btn.setAutoDefault(False)
        toolbar.addWidget(self.close_btn)
        layout.addLayout(toolbar)
        self.view = _ImageView(self._source_pixmap, self)
        layout.addWidget(self.view, 1)
        tip = QLabel("Fare tekerleğiyle yakınlaştırın; basılı tutup sürükleyerek gezinin.")
        layout.addWidget(tip)
        self.zoom_out_btn.clicked.connect(lambda: self.view.zoom(0.8))
        self.zoom_in_btn.clicked.connect(lambda: self.view.zoom(1.25))
        self.fit_btn.clicked.connect(self.view.fit_image)
        self.actual_size_btn.clicked.connect(self.view.actual_size)
        self.close_btn.clicked.connect(self.close)
        self.view.scale_changed.connect(self._update_scale)
        self._update_scale(self.view.zoom_factor)

    @property
    def source_pixmap(self) -> QPixmap:
        return QPixmap(self._source_pixmap)

    def set_image(self, pixmap: QPixmap, title: str):
        """Explicitly inspect another preview in the same viewer window."""
        self._source_pixmap = QPixmap(pixmap)
        self.setWindowTitle(title)
        self.view.set_image(self._source_pixmap)

    def _update_scale(self, scale: float):
        self.zoom_label.setText(f"%{scale * 100:.0f}")
        self.zoom_out_btn.setEnabled(scale > self.view.MIN_SCALE)
        self.zoom_in_btn.setEnabled(scale < self.view.MAX_SCALE)

    def showEvent(self, event):
        super().showEvent(event)
        if self.view._fit_mode:
            self.view.fit_image()


class ImagePreviewLabel(QLabel):
    """Aspect-preserving preview opened by a left click or Enter/Space.

    ``image_opened`` and ``image_closed`` signal the lifetime of one modeless
    window, not individual clicks. They can pause and resume payment timers.
    ``clear_image`` removes stale preview data without changing an open viewer.
    """

    image_opened = Signal()
    image_closed = Signal()

    def __init__(self, text: str = "", parent=None, *, title: str = "Görsel"):
        super().__init__(text, parent)
        self._source_pixmap = QPixmap()
        self._image_path = None
        self._title = title
        self._viewer = None
        self._viewer_token = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def source_pixmap(self) -> QPixmap:
        if self._image_path is not None:
            original = QPixmap(str(self._image_path))
            if original.isNull():
                self.clear_image()
            return original
        return QPixmap(self._source_pixmap)

    def sizeHint(self) -> QSize:
        # QLabel normally reports the pixmap's size, which can expand a camera
        # grid repeatedly as the application window changes size.
        return QSize(160, 100)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def has_image(self) -> bool:
        return not self._source_pixmap.isNull()

    def set_image(self, pixmap: QPixmap, title: str | None = None):
        self._image_path = None
        if title is not None:
            self._title = title
        if pixmap.isNull():
            self.clear_image()
            return
        self._source_pixmap = QPixmap(pixmap)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Görseli büyütmek için tıklayın")
        self._render_preview()

    def set_image_path(self, path, title: str | None = None, *, preview: QPixmap | None = None):
        """Load a file, or retain only its path and an optional small thumbnail.

        History lists can supply ``preview`` to avoid keeping every original
        capture in memory. The original is loaded only when opened or requested
        via ``source_pixmap``. Missing files clear the previous image.
        """
        if title is not None:
            self._title = title
        try:
            image_path = Path(path) if path else None
            if preview is not None and not preview.isNull() and image_path and image_path.is_file():
                self.set_image(preview)
                self._image_path = image_path
                return
            pixmap = QPixmap(str(image_path)) if image_path else QPixmap()
        except (TypeError, ValueError, OSError):
            pixmap = QPixmap()
        if pixmap.isNull():
            self.clear_image()
        else:
            self.set_image(pixmap)

    def clear_image(self, text: str = "Görsel bulunamadı"):
        self._source_pixmap = QPixmap()
        self._image_path = None
        super().clear()
        super().setText(text)
        self.unsetCursor()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("")

    def _render_preview(self):
        if not self.has_image():
            return
        size = self.contentsRect().size()
        if size.width() > 0 and size.height() > 0:
            super().setPixmap(self._source_pixmap.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def show_image(self):
        if not self.has_image():
            return
        original = self.source_pixmap
        if original.isNull():
            return
        if self._viewer is not None:
            self._viewer.set_image(original, self._title)
            if self._viewer.isMinimized():
                self._viewer.showNormal()
            else:
                self._viewer.show()
            self._viewer.raise_()
            self._viewer.activateWindow()
            return
        self._viewer = ImageViewerDialog(original, self._title, self.window())
        token = object()
        self._viewer_token = token
        label_ref = weakref.ref(self)

        def closed(*_args):
            label = label_ref()
            # A parent may destroy both the label and viewer together. Never
            # emit a signal on a Python wrapper whose Qt object is gone.
            if label is not None and isValid(label):
                label._viewer_closed(token)

        self._viewer.finished.connect(closed)
        self._viewer.destroyed.connect(closed)
        self._viewer.show()
        self.image_opened.emit()

    def close_viewer(self):
        if self._viewer is not None:
            self._viewer.close()

    def _viewer_closed(self, token):
        # A closed dialog may be deleted after another viewer has been opened.
        # Its delayed destroyed signal must not clear the new viewer handle.
        if self._viewer is not None and token is self._viewer_token:
            self._viewer = None
            self._viewer_token = None
            self.image_closed.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.has_image():
            self.show_image()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
                and self.has_image()):
            self.show_image()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_preview()
