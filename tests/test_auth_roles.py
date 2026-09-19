"""Madde 28: Kullanici rol ve yetki testleri (madde 16)."""
import secrets

import pytest

from app.services.auth import (
    has_perm, require, PermissionDenied, ROLES, PERMISSIONS,
    can_manage_role, require_manage_role,
)


class TestRoles:
    def test_bes_rol_tanimli(self):
        assert len(ROLES) == 5
        assert "kasiyer" in ROLES
        assert "guvenlik" in ROLES
        assert "muhasebe" in ROLES
        assert "yonetici" in ROLES
        assert "sistem_yoneticisi" in ROLES


class TestKasiyerPermissions:
    user = {"username": "k1", "role": "kasiyer"}

    def test_odeme_alabilir(self):
        assert has_perm(self.user, "record_payment")

    def test_vardiya_yonetebilir(self):
        assert has_perm(self.user, "manage_own_shift")

    def test_bekleyen_cikislari_gorebilir(self):
        assert has_perm(self.user, "view_pending_exits")

    def test_tarife_degistirebilir(self):
        assert has_perm(self.user, "manage_tariffs")

    def test_smtp_goremez(self):
        assert not has_perm(self.user, "manage_smtp")

    def test_operasyon_kullanicilarini_yonetebilir(self):
        assert has_perm(self.user, "manage_users")

    def test_yalnizca_ag_ayarlari_kapali(self):
        admin_permissions = PERMISSIONS["sistem_yoneticisi"]
        assert admin_permissions - PERMISSIONS["kasiyer"] == {
            "manage_smtp", "manage_devices"}


class TestGuvenlikPermissions:
    user = {"username": "g1", "role": "guvenlik"}

    def test_canli_gorur(self):
        assert has_perm(self.user, "view_live")

    def test_plaka_dogrulayabilir(self):
        assert has_perm(self.user, "verify_plate")

    def test_bariyer_acabilir(self):
        assert has_perm(self.user, "manual_barrier_open")

    def test_odeme_yapamaz(self):
        assert not has_perm(self.user, "record_payment")

    def test_tarife_degistiremez(self):
        assert not has_perm(self.user, "manage_tariffs")


class TestMuhasebePermissions:
    user = {"username": "m1", "role": "muhasebe"}

    def test_raporlari_gorebilir(self):
        assert has_perm(self.user, "view_reports")

    def test_odemeleri_gorebilir(self):
        assert has_perm(self.user, "view_payments")

    def test_vardiyalari_gorebilir(self):
        assert has_perm(self.user, "view_shifts")

    def test_excel_pdf_alabilir(self):
        assert has_perm(self.user, "export_reports")

    def test_odeme_kaydedemez(self):
        assert not has_perm(self.user, "record_payment")


class TestYoneticiPermissions:
    user = {"username": "y1", "role": "yonetici"}

    def test_tarife_yonetir(self):
        assert has_perm(self.user, "manage_tariffs")

    def test_kullanici_yonetir(self):
        assert has_perm(self.user, "manage_users")

    def test_abonelik_yonetir(self):
        assert has_perm(self.user, "manage_subscriptions")

    def test_odeme_iptal_eder(self):
        assert has_perm(self.user, "cancel_payment")

    def test_oturum_duzelteme(self):
        assert has_perm(self.user, "edit_sessions")

    def test_oturum_tekrar_acar(self):
        assert has_perm(self.user, "reopen_session")


class TestSistemYoneticisiPermissions:
    user = {"username": "s1", "role": "sistem_yoneticisi"}

    def test_smtp_yonetir(self):
        assert has_perm(self.user, "manage_smtp")

    def test_cihaz_yonetir(self):
        assert has_perm(self.user, "manage_devices")

    def test_ayar_yonetir(self):
        assert has_perm(self.user, "manage_settings")

    def test_audit_gorur(self):
        assert has_perm(self.user, "view_audit_logs")

    def test_zamanlama_yonetir(self):
        assert has_perm(self.user, "manage_schedule")


class TestRequireFunction:
    def test_yetkili_islem_gecer(self, admin):
        require(admin, "manage_smtp")  # hata firlatmamali

    def test_yetkisiz_islem_hata(self, cashier):
        with pytest.raises(PermissionDenied):
            require(cashier, "manage_smtp")

    def test_none_user_hata(self):
        with pytest.raises(PermissionDenied):
            require(None, "view_live")


class TestUnauthorizedBarrier:
    """Yetkisiz manuel bariyer acma (madde 13)."""

    def test_kasiyer_bariyer_acabilir(self, cashier):
        require(cashier, "manual_barrier_open")

    def test_muhasebe_bariyer_acamaz(self, accountant):
        with pytest.raises(PermissionDenied):
            require(accountant, "manual_barrier_open")

    def test_guvenlik_bariyer_acabilir(self, svc, isolated_db, security):
        assert has_perm(security, "manual_barrier_open")


class TestUserManagementBoundary:
    @pytest.mark.parametrize("role", ["kasiyer", "guvenlik", "muhasebe"])
    def test_kasiyer_operasyon_hesabi_yonetir(self, cashier, role):
        require_manage_role(cashier, role)

    @pytest.mark.parametrize("role", ["yonetici", "sistem_yoneticisi", "bilinmeyen"])
    def test_kasiyer_ag_yetkisi_kazanamaz(self, cashier, role):
        assert not can_manage_role(cashier, role)
        with pytest.raises(PermissionDenied):
            require_manage_role(cashier, role)

    @pytest.mark.parametrize("role", ROLES)
    def test_admin_tum_rolleri_yonetir(self, admin, role):
        require_manage_role(admin, role)

    @pytest.mark.parametrize("user", [None, {"role": "guvenlik"}, {"role": "muhasebe"}])
    def test_kullanici_yonetimi_yetki_ister(self, user):
        with pytest.raises(PermissionDenied):
            require_manage_role(user, "kasiyer")


class TestPasswordStorage:
    def test_yeni_sifre_pbkdf2_ile_saklanir(self, isolated_db):
        isolated_db.create_user("guclu", "Guvenli-123", "yonetici")
        with isolated_db._connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username=?", ("guclu",)
            ).fetchone()
        assert row["password_hash"].startswith("pbkdf2_sha256$310000$")
        assert isolated_db.verify_user("guclu", "Guvenli-123") is not None
        assert isolated_db.verify_user("guclu", "yanlis") is None

    def test_eski_sha256_sifre_ilk_giriste_yukseltilir(self, isolated_db):
        salt = secrets.token_hex(16)
        legacy = isolated_db._legacy_hash_password("Eski-123", salt)
        with isolated_db._connect() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,salt,role,created_at) "
                "VALUES(?,?,?,?,datetime('now'))",
                ("eski", legacy, salt, "kasiyer"),
            )
        assert isolated_db.verify_user("eski", "Eski-123") is not None
        with isolated_db._connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username=?", ("eski",)
            ).fetchone()
        assert row["password_hash"].startswith("pbkdf2_sha256$")
