"""Yeni kurulum ve mevcut kullanicilar icin varsayilan admin sifresi gecisi."""
import secrets

import pytest


MARKER = "default_admin_password_20260919_v1"


def _old_install(db, password="admin123", *, legacy=False, role="sistem_yoneticisi"):
    salt = secrets.token_hex(16)
    password_hash = (
        db._legacy_hash_password(password, salt) if legacy
        else db._hash_password(password, salt)
    )
    with db._connect() as conn:
        conn.execute("DELETE FROM system_settings WHERE key=?", (MARKER,))
        conn.execute(
            "UPDATE users SET password_hash=?, salt=?, role=? WHERE username='admin'",
            (password_hash, salt, role),
        )


def _stored_user(db, username):
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def test_fresh_database_uses_admin_admin(isolated_db):
    assert isolated_db.verify_user("admin", "admin") == {
        "username": "admin", "role": "sistem_yoneticisi",
    }
    assert isolated_db.verify_user("admin", "admin123") is None
    assert isolated_db.get_setting(MARKER) == "1"
    assert _stored_user(isolated_db, "admin")["password_hash"].startswith("pbkdf2_sha256$")


@pytest.mark.parametrize("legacy", [False, True])
def test_old_default_migrates_without_changing_identity_or_role(isolated_db, legacy):
    _old_install(isolated_db, legacy=legacy, role="yonetici")
    before = _stored_user(isolated_db, "admin")
    isolated_db.create_user("kasiyer1", "admin123", role="kasiyer")
    other_before = _stored_user(isolated_db, "kasiyer1")

    isolated_db.init_db()

    assert isolated_db.verify_user("admin", "admin") == {
        "username": "admin", "role": "yonetici",
    }
    assert isolated_db.verify_user("admin", "admin123") is None
    after = _stored_user(isolated_db, "admin")
    assert (after["id"], after["created_at"], after["role"]) == (
        before["id"], before["created_at"], before["role"],
    )
    assert after["password_hash"].startswith("pbkdf2_sha256$")
    assert _stored_user(isolated_db, "kasiyer1") == other_before
    assert isolated_db.get_setting(MARKER) == "1"


@pytest.mark.parametrize("legacy", [False, True])
def test_custom_admin_password_is_preserved_exactly(isolated_db, legacy):
    _old_install(isolated_db, password="Ozel-sifre-987", legacy=legacy)
    before = _stored_user(isolated_db, "admin")

    isolated_db.init_db()

    assert _stored_user(isolated_db, "admin") == before
    assert isolated_db.verify_user("admin", "admin") is None
    assert isolated_db.verify_user("admin", "Ozel-sifre-987") is not None
    assert isolated_db.get_setting(MARKER) == "1"


def test_migration_does_not_overwrite_later_intentional_password_change(isolated_db):
    _old_install(isolated_db)
    isolated_db.init_db()
    user_id = _stored_user(isolated_db, "admin")["id"]
    isolated_db.update_user_password(user_id, "admin123")
    before = _stored_user(isolated_db, "admin")

    isolated_db.init_db()
    isolated_db.init_db()

    assert _stored_user(isolated_db, "admin") == before
    assert isolated_db.verify_user("admin", "admin123") is not None
    assert isolated_db.verify_user("admin", "admin") is None


def test_custom_password_also_consumes_the_migration_once(isolated_db):
    _old_install(isolated_db, password="Ozel-sifre-987")
    isolated_db.init_db()
    user_id = _stored_user(isolated_db, "admin")["id"]
    isolated_db.update_user_password(user_id, "admin123")

    isolated_db.init_db()

    assert isolated_db.verify_user("admin", "admin123") is not None
    assert isolated_db.verify_user("admin", "admin") is None


def test_existing_accounts_without_admin_do_not_gain_a_new_account(isolated_db):
    _old_install(isolated_db)
    isolated_db.create_user("yonetici1", "Ozel-sifre-123", role="yonetici")
    other_before = _stored_user(isolated_db, "yonetici1")
    with isolated_db._connect() as conn:
        conn.execute("DELETE FROM users WHERE username='admin'")

    isolated_db.init_db()

    assert _stored_user(isolated_db, "admin") is None
    assert _stored_user(isolated_db, "yonetici1") == other_before
    assert isolated_db.get_setting(MARKER) == "1"
