"""Reports display grouped plates while retaining canonical report input."""
from copy import deepcopy
from datetime import date

from openpyxl import load_workbook
import pytest


@pytest.fixture
def report_data(isolated_db):
    from app.reports.queries import build_daily_report_data

    data = build_daily_report_data(date(2026, 9, 19))
    entry = {
        "plaka": "34GGG01", "giris": "2026-09-19T10:00:00+03:00",
        "kapi": "GIRIS-1", "arac_tipi": "CAR", "abone": "Normal", "guven": "96%",
    }
    exit_record = {
        **entry, "cikis": "2026-09-19T12:00:00+03:00", "sure": "2 saat",
        "ucret": "100.00", "odeme_yontemi": "NAKIT", "kasiyer": "kasiyer1",
    }
    data["entries"] = [entry]
    data["exits"] = [exit_record]
    data["inside"] = [{**entry, "plaka": "06AB1234", "sure": "1 saat"}]
    data["payments"] = [{
        "islem_no": "12345678", "plaka": "34GGG01", "tutar": "100.00",
        "yontem": "NAKIT", "kasiyer": "kasiyer1", "saat": exit_record["cikis"],
        "durum": "TAMAM",
    }]
    manual = {
        "kullanici": "kasiyer1", "kapi": "CIKIS-1", "plaka": "34GGG01",
        "neden": "Manuel acma", "saat": exit_record["cikis"],
    }
    data["manual_ops"] = [manual, {**manual, "plaka": "-"}]
    return data


def test_excel_all_plate_columns_are_grouped(report_data, tmp_path, monkeypatch):
    from app.reports import excel_report

    monkeypatch.setattr(excel_report, "REPORTS_DIR", str(tmp_path))
    original = deepcopy(report_data)
    path = excel_report.build_excel(report_data)
    workbook = load_workbook(path)
    try:
        assert workbook["Girisler"]["A2"].value == "34 GGG 01"
        assert workbook["Cikislar"]["A2"].value == "34 GGG 01"
        assert workbook["Iceride Kalanlar"]["A2"].value == "06 AB 1234"
        assert workbook["Tahsilatlar"]["B2"].value == "34 GGG 01"
        assert workbook["Manuel Islemler"]["C2"].value == "34 GGG 01"
        assert workbook["Manuel Islemler"]["C3"].value == "-"
    finally:
        workbook.close()
    assert report_data == original


def test_pdf_exit_card_prints_grouped_plate(report_data, tmp_path, monkeypatch):
    from pathlib import Path
    from reportlab import rl_config
    from app.reports import pdf_report

    monkeypatch.setattr(pdf_report, "REPORTS_DIR", str(tmp_path))
    # Keep actual PDF text streams inspectable without a new runtime dependency.
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    original = deepcopy(report_data)
    output = Path(pdf_report.build_pdf(report_data)).read_bytes()
    assert output.startswith(b"%PDF-")
    assert b"34 GGG 01" in output
    assert b"34GGG01" not in output
    assert report_data == original
