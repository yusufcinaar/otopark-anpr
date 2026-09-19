"""Uygulama genelinde kullanilan modern koyu tema (QSS)."""

COLORS = {
    "bg": "#eef2f7",
    "surface": "#ffffff",
    "surface_alt": "#f5f7fb",
    "border": "#d7dee8",
    "text": "#172033",
    "text_muted": "#64748b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "success": "#16a34a",
    "danger": "#dc2626",
    "warning": "#d97706",
}

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Segoe UI Semibold", sans-serif;
    color: {COLORS['text']};
}}

QMainWindow, #centralWidget {{
    background-color: {COLORS['bg']};
}}

QMenuBar {{
    background-color: {COLORS['surface']};
    color: {COLORS['text']};
    border-bottom: 1px solid {COLORS['border']};
    padding: 2px;
}}
QMenuBar::item {{
    padding: 6px 12px;
    background: transparent;
    border-radius: 6px;
}}
QMenuBar::item:selected {{
    background-color: {COLORS['surface_alt']};
}}
QMenu {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 20px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {COLORS['accent']};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background: {COLORS['border']};
    margin: 4px 6px;
}}

QLabel {{
    background: transparent;
}}

QLabel#appTitle {{
    font-size: 20px;
    font-weight: 600;
    color: {COLORS['text']};
}}

QLabel#appSubtitle {{
    font-size: 12px;
    color: {COLORS['text_muted']};
}}

QFrame#cameraCard {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
}}

QFrame#controlStrip {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}

QLabel#cameraFeed {{
    background-color: #dce4ee;
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    color: {COLORS['text_muted']};
    font-size: 12px;
}}

QLabel#plateBadge {{
    background-color: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 10px;
    font-family: Consolas, monospace;
    font-size: 15px;
    font-weight: 600;
    color: {COLORS['accent_hover']};
}}

QLabel#laneBadge {{
    background-color: rgba(59, 130, 246, 0.15);
    color: {COLORS['accent_hover']};
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#speedBadge {{
    background-color: rgba(245, 158, 11, 0.12);
    color: {COLORS['warning']};
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 5px;
    padding: 4px 7px;
    font-size: 10px;
    font-weight: 700;
}}

QPushButton {{
    background-color: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    color: {COLORS['text']};
}}
QPushButton:hover {{
    background-color: #e7edf6;
    border-color: {COLORS['accent']};
}}
QPushButton:pressed {{
    background-color: #dbe4f0;
}}
QPushButton:disabled {{
    color: {COLORS['text_muted']};
    background-color: {COLORS['surface']};
    border-color: {COLORS['border']};
}}

QPushButton#primaryBtn {{
    background-color: {COLORS['accent']};
    border: none;
    color: white;
}}
QPushButton#primaryBtn:hover {{
    background-color: {COLORS['accent_hover']};
}}
QPushButton#primaryBtn:disabled {{
    background-color: #cbd5e1;
    color: #64748b;
}}

QPushButton#successBtn {{
    background-color: {COLORS['success']};
    border: none;
    color: white;
}}
QPushButton#successBtn:hover {{
    background-color: #34d16f;
}}
QPushButton#successBtn:disabled {{
    background-color: #d1fae5;
    color: #64748b;
}}

QPushButton#dangerBtn {{
    background-color: rgba(239, 68, 68, 0.12);
    border: 1px solid {COLORS['danger']};
    color: #fca5a5;
}}
QPushButton#dangerBtn:hover {{
    background-color: {COLORS['danger']};
    color: white;
}}

QComboBox {{
    background-color: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 6px 8px;
    font-size: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent']};
}}

QListWidget#logList {{
    background-color: {COLORS['surface']};
    border: none;
    border-radius: 8px;
    padding: 4px;
    font-size: 12px;
}}
QListWidget#logList::item {{
    padding: 7px 8px;
    border-radius: 6px;
    margin-bottom: 2px;
}}
QListWidget#logList::item:selected {{
    background-color: {COLORS['surface_alt']};
}}

QTableWidget {{
    background-color: {COLORS['surface']};
    border: none;
    border-radius: 8px;
    gridline-color: transparent;
    font-size: 12px;
    selection-background-color: rgba(59, 130, 246, 0.18);
    selection-color: {COLORS['text']};
    alternate-background-color: {COLORS['surface_alt']};
}}
QTableWidget::item {{
    padding: 6px;
    border-bottom: 1px solid {COLORS['border']};
}}
QHeaderView::section {{
    background-color: {COLORS['surface_alt']};
    color: {COLORS['text_muted']};
    font-size: 11px;
    font-weight: 600;
    padding: 8px;
    border: none;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableCornerButton::section {{
    background-color: {COLORS['surface_alt']};
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['accent']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QDialog {{
    background-color: {COLORS['surface']};
}}
QDoubleSpinBox, QSpinBox {{
    background-color: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 4px 6px;
}}

QMessageBox {{
    background-color: {COLORS['surface']};
}}
"""
