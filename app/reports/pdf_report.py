"""Profesyonel, fotograflı gunluk otopark PDF raporu."""
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import REPORTS_DIR
from app.services.plate_utils import format_plate


def _fonts():
    candidates = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    ]
    for regular_path, bold_path in candidates:
        if os.path.isfile(regular_path) and os.path.isfile(bold_path):
            try:
                pdfmetrics.registerFont(TTFont("OtoparkRegular", regular_path))
                pdfmetrics.registerFont(TTFont("OtoparkBold", bold_path))
                return "OtoparkRegular", "OtoparkBold"
            except Exception:
                pass
    return "Helvetica", "Helvetica-Bold"


def _fmt_time(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError):
        return str(value).replace("T", " ")[:19]


def _vehicle_image(path: str, regular: str):
    if path and os.path.isfile(path):
        try:
            image = Image(path)
            image._restrictSize(4.05 * cm, 2.65 * cm)
            return image
        except Exception:
            pass
    return Paragraph("Görsel bulunamadı", ParagraphStyle(
        "MissingImage", fontName=regular, fontSize=8,
        textColor=colors.HexColor("#64748B"), alignment=TA_CENTER,
        leading=10, spaceBefore=28))


def _record_card(record: dict, small, regular: str):
    speed = record.get("cikis_hizi")
    speed_text = f"{float(speed):.0f} km/sa" if speed not in (None, "") else "-"
    details = Paragraph(
        f"<b>PLAKA:</b> {format_plate(record.get('plaka')) or '-'}<br/>"
        f"<b>GİRİŞ:</b> {_fmt_time(record.get('giris'))}<br/>"
        f"<b>ÇIKIŞ:</b> {_fmt_time(record.get('cikis'))}<br/>"
        f"<b>SÜRE:</b> {record.get('sure') or '-'}<br/>"
        f"<b>ÜCRET:</b> {record.get('ucret') or '0.00'} TL<br/>"
        f"<b>HIZ:</b> {speed_text}<br/>"
        f"<b>AÇIKLAMA:</b> {record.get('aciklama') or '-'}<br/>"
        f"<b>KAMERA:</b> {record.get('kamera') or '-'}<br/>"
        f"<b>KULLANICI:</b> {record.get('kasiyer') or 'SİSTEM'}", small)
    card = Table(
        [[_vehicle_image(record.get("cikis_gorseli") or record.get("giris_gorseli") or "", regular), details]],
        colWidths=[4.2 * cm, 4.35 * cm], rowHeights=[3.35 * cm])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.65, colors.HexColor("#94A3B8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F8FAFC")),
    ]))
    return card


def build_pdf(data: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"Otopark_Gunluk_Rapor_{data['report_date']}.pdf")
    regular, bold = _fonts()
    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=1.15 * cm, rightMargin=1.15 * cm,
        topMargin=1.25 * cm, bottomMargin=1.35 * cm,
        title=f"Otopark Günlük Raporu - {data['report_date']}",
        author=data.get("location_name") or "Otopark Yönetim Sistemi")
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], fontName=bold,
                           fontSize=20, leading=24, textColor=colors.HexColor("#0F172A"),
                           alignment=TA_LEFT, spaceAfter=4)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontName=regular,
                              fontSize=9, leading=12, textColor=colors.HexColor("#475569"))
    heading = ParagraphStyle("Heading", parent=styles["Heading2"], fontName=bold,
                             fontSize=12, leading=15, textColor=colors.HexColor("#1D4ED8"),
                             spaceBefore=10, spaceAfter=7)
    normal = ParagraphStyle("Body", parent=styles["Normal"], fontName=regular,
                            fontSize=8.2, leading=10.2)
    small = ParagraphStyle("Card", parent=normal, fontName=regular,
                           fontSize=6.6, leading=8.1, textColor=colors.HexColor("#0F172A"))

    story = [
        Paragraph("OTOPARK GÜNLÜK RAPORU", title),
        Paragraph(f"<b>{data.get('location_name') or 'OTOPARK'}</b>", subtitle),
        Spacer(1, 7),
    ]
    period = Table([[
        "Başlangıç", _fmt_time(data.get("period_start")),
        "Bitiş", _fmt_time(data.get("period_end")),
        "Kayıt", str(len(data.get("exits", [])))
    ]], colWidths=[1.8 * cm, 3.55 * cm, 1.3 * cm, 3.55 * cm, 1.2 * cm, 1.2 * cm])
    period.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("FONTNAME", (0, 0), (0, 0), bold), ("FONTNAME", (2, 0), (2, 0), bold),
        ("FONTNAME", (4, 0), (4, 0), bold), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([period, Paragraph("Günlük Özet", heading)])

    s = data["summary"]
    summary_rows = [
        ["Toplam Giriş", s["toplam_giris"], "Toplam Çıkış", s["toplam_cikis"], "İçeride Kalan", s["iceride_kalan"]],
        ["Normal / Abone", f"{s['normal_arac']} / {s['abone_arac']}", "Toplam Tahsilat", f"{s['toplam_tahsilat']} TL", "Nakit", f"{s['nakit']} TL"],
        ["Kredi Kartı", f"{s['kredi_karti']} TL", "Banka Kartı", f"{s['banka_karti']} TL", "İptal / İade", f"{s['iptal_iade']} TL"],
    ]
    summary = Table(summary_rows, colWidths=[2.55 * cm, 1.8 * cm] * 3)
    summary.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("FONTNAME", (0, 0), (0, -1), bold), ("FONTNAME", (2, 0), (2, -1), bold),
        ("FONTNAME", (4, 0), (4, -1), bold),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1E3A8A")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#1E3A8A")),
        ("BACKGROUND", (4, 0), (4, -1), colors.HexColor("#1E3A8A")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.white),
        ("TEXTCOLOR", (4, 0), (4, -1), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.6),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary)

    if data.get("warnings"):
        story.append(Paragraph("Sistem Uyarıları", heading))
        for warning in data["warnings"]:
            story.append(Paragraph(f"• {warning}", normal))

    exits = data.get("exits", [])
    story.extend([Paragraph("Fotoğraflı Çıkış Kayıtları", heading), Spacer(1, 2)])
    if exits:
        card_rows = []
        for index in range(0, len(exits), 2):
            left = _record_card(exits[index], small, regular)
            right = _record_card(exits[index + 1], small, regular) if index + 1 < len(exits) else ""
            card_rows.append([left, right])
        cards = Table(card_rows, colWidths=[8.65 * cm, 8.65 * cm], hAlign="LEFT")
        cards.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(cards)
    else:
        story.append(Paragraph("Bu tarih aralığında çıkış kaydı bulunmuyor.", normal))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(document.leftMargin, 0.65 * cm,
                          f"{data.get('location_name') or 'Otopark'} - Otomatik ANPR Raporu")
        canvas.drawRightString(A4[0] - document.rightMargin, 0.65 * cm, f"Sayfa {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
