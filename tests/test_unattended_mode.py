"""Operatorsuz 7/24 kamera ve bariyer akisi."""
from tests.conftest import make_event


def test_orta_guven_operator_beklemeden_giris(svc, isolated_db):
    isolated_db.set_setting("unattended_mode", "1")
    result = svc.ingest_camera_event(make_event(confidence=80))
    assert result["status"] == "ENTRY_OK"
    assert isolated_db.list_inside_sessions()[0]["plate"] == "34ABC123"


def test_operatorsuz_giris_ve_cikis_otomatik_tamamlanir(svc, isolated_db):
    isolated_db.set_setting("unattended_mode", "1")
    isolated_db.set_setting("automatic_free_exit", "1")
    entry = svc.ingest_camera_event(make_event(confidence=95))
    assert entry["status"] == "ENTRY_OK"

    exit_event = make_event(
        direction="EXIT", plate="34ABC123", confidence=95,
        gate="CIKIS-1", camera="LPR-CIKIS-01")
    result = svc.ingest_camera_event(exit_event)
    assert result["status"] == "AUTOMATIC_EXIT"
    assert isolated_db.get_session(entry["session_id"])["status"] == "TAMAMLANDI"


def test_dusuk_guven_kaydedilir_bariyer_acilmaz(svc, isolated_db):
    isolated_db.set_setting("unattended_mode", "1")
    result = svc.ingest_camera_event(make_event(confidence=40))
    assert result["status"] == "LOW_CONFIDENCE_RECORDED"
    assert isolated_db.list_inside_sessions() == []


def test_serbest_geciste_kameralar_giris_cikisi_kaydeder(svc, isolated_db):
    isolated_db.set_setting("unattended_mode", "1")
    isolated_db.set_setting("automatic_free_exit", "0")
    isolated_db.set_setting("free_pass_mode", "1")

    entry = svc.ingest_camera_event(make_event(plate="34SG123", confidence=95))
    assert entry["status"] == "ENTRY_OK"
    exit_event = make_event(
        direction="EXIT", plate="34SG123", confidence=95,
        gate="CIKIS-1", camera="LPR-CIKIS-01")
    result = svc.ingest_camera_event(exit_event)

    assert result["status"] == "FREE_PASS_EXIT"
    session = isolated_db.get_session(entry["session_id"])
    assert session["entry_lane"] == "GIRIS-1"
    assert session["exit_lane"] == "CIKIS-1"
    assert session["status"] == "TAMAMLANDI"
    assert isolated_db.list_inside_sessions() == []
