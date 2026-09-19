"""Rol tabanli yetkilendirme.

Bu masaustu uygulamada 'backend' katmani servis fonksiyonlaridir; yetki kontrolu
yalnizca arayuzde buton gizleyerek degil, servis cagrilarinda da uygulanir.
"""

ROLES = ["kasiyer", "guvenlik", "muhasebe", "yonetici", "sistem_yoneticisi"]

PERMISSIONS = {
    "kasiyer": {
        "view_pending_exits", "record_payment", "manage_own_shift", "view_live",
        "verify_plate", "manual_barrier_open", "view_reports", "view_payments",
        "view_shifts", "export_reports", "manage_tariffs", "manage_users",
        "manage_subscriptions", "cancel_payment", "edit_sessions",
        "manage_email_recipients", "reopen_session", "manage_blacklist",
        "manage_settings", "view_audit_logs", "manage_schedule",
        "send_test_email", "trigger_report",
    },
    "guvenlik": {
        "view_live", "verify_plate", "manual_barrier_open",
    },
    "muhasebe": {
        "view_reports", "view_payments", "view_shifts", "export_reports",
    },
    "yonetici": {
        "view_pending_exits", "record_payment", "manage_own_shift", "view_live",
        "verify_plate", "manual_barrier_open", "view_reports", "view_payments",
        "view_shifts", "export_reports", "manage_tariffs", "manage_users",
        "manage_subscriptions", "cancel_payment", "edit_sessions",
        "manage_email_recipients", "reopen_session", "manage_blacklist",
        "manage_smtp", "manage_devices", "manage_settings",
    },
    "sistem_yoneticisi": set(),  # asagida tum yetkiler verilir
}

_ALL_PERMS = set().union(*[p for p in PERMISSIONS.values()]) | {
    "manage_smtp", "manage_devices", "manage_settings", "view_audit_logs",
    "manage_schedule", "send_test_email", "trigger_report",
}
PERMISSIONS["sistem_yoneticisi"] = set(_ALL_PERMS)
# yonetici da rapor tetikleyebilsin ve audit gorebilsin
PERMISSIONS["yonetici"] |= {
    "view_audit_logs", "trigger_report", "send_test_email", "manage_schedule",
}


class PermissionDenied(Exception):
    pass


def has_perm(user: dict | None, perm: str) -> bool:
    if not user:
        return False
    return perm in PERMISSIONS.get(user.get("role", ""), set())


def require(user: dict | None, perm: str):
    if not has_perm(user, perm):
        role = user.get("role", "?") if user else "yok"
        raise PermissionDenied(f"Bu islem icin yetkiniz yok (rol: {role}, gereken: {perm}).")


def can_manage_role(user: dict | None, role: str) -> bool:
    """Kullanici yonetimi kisinin sahip olmadigi yetkileri kazandirmamalidir.

    Kasiyer operasyon hesaplari acabilir; yonetici sifresini degistirerek veya
    yeni yonetici olusturarak ag ayarlari kisitlamasini asamaz.
    """
    if not has_perm(user, "manage_users") or role not in ROLES:
        return False
    return PERMISSIONS[role].issubset(PERMISSIONS.get(user.get("role", ""), set()))


def require_manage_role(user: dict | None, role: str):
    require(user, "manage_users")
    if not can_manage_role(user, role):
        raise PermissionDenied(
            "Kendi yetkilerinizden daha fazla yetkiye sahip kullanici "
            "olusturamaz, silemez veya sifresini degistiremezsiniz.")
