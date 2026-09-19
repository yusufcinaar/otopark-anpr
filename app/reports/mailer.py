"""SMTP e-posta gonderimi: HTML govde + Excel/PDF ekleri.

SMTP bilgileri .env / environment variable'dan gelir; sifre asla loglanmaz.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import SMTP as ENV_SMTP
from app import db
from app.security import unprotect_secret


class SmtpNotConfigured(Exception):
    pass


class SmtpAuthenticationFailed(Exception):
    """SMTP saglayicisi kullanici adi/parolayi reddetti."""

    pass


class SmtpRelayRejected(Exception):
    """IP yetkili SMTP relay mesaji kabul etmedi."""

    pass


def _build_html(data: dict) -> str:
    s = data["summary"]
    def row(k, v):
        return f"<tr><td style='padding:6px 12px;border:1px solid #ccc'>{k}</td>" \
               f"<td style='padding:6px 12px;border:1px solid #ccc;text-align:right'>{v}</td></tr>"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
    <h2>Otopark Gunluk Giris-Cikis Raporu - {data['report_date']}</h2>
    <table style="border-collapse:collapse">
      {row('Toplam Giris', s['toplam_giris'])}
      {row('Toplam Cikis', s['toplam_cikis'])}
      {row('Iceride Kalan', s['iceride_kalan'])}
      {row('Normal / Abone', f"{s['normal_arac']} / {s['abone_arac']}")}
      {row('Toplam Tahsilat', s['toplam_tahsilat'] + ' TL')}
      {row('Nakit', s['nakit'] + ' TL')}
      {row('Kredi Karti', s['kredi_karti'] + ' TL')}
      {row('Banka Karti', s['banka_karti'] + ' TL')}
      {row('Havale/EFT', s['havale_eft'] + ' TL')}
      {row('Iptal/Iade', s['iptal_iade'] + ' TL')}
      {row('Manuel Bariyer Acma', s['manuel_bariyer'])}
      {row('Okunamayan Plaka', s['okunamayan_plaka'])}
      {row('Elle Duzeltilen Plaka', s['elle_duzeltilen'])}
    </table>
    """
    if data["warnings"]:
        html += "<h3>Sistem Uyarilari</h3><ul>"
        html += "".join(f"<li>{w}</li>" for w in data["warnings"])
        html += "</ul>"
    html += "<p>Detayli tablolar ekteki Excel ve PDF dosyalarindadir.</p></body></html>"
    return html


def get_smtp_settings() -> dict:
    """Program ekranindaki ayarlari kullan; yoksa eski .env ayarlarina geri don."""
    saved_host = db.get_setting("smtp_host", "")
    host = saved_host or ENV_SMTP["host"]
    from_email = db.get_setting("smtp_from_email", "") or ENV_SMTP["from_email"]
    username = db.get_setting("smtp_username", "") or ENV_SMTP["username"]
    encrypted = db.get_setting("smtp_password_enc", "")
    try:
        password = unprotect_secret(encrypted) if encrypted else ENV_SMTP["password"]
    except Exception:
        password = ""
    return {
        "host": host,
        "port": int(db.get_setting("smtp_port", "") or ENV_SMTP["port"]),
        "provider": db.get_setting("smtp_provider", "custom") or "custom",
        "auth_mode": (
            db.get_setting("smtp_auth_mode", "password") if saved_host
            else ENV_SMTP.get("auth_mode", "password")
        ) or ("relay" if host.lower() == "smtp-relay.gmail.com" else "password"),
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": db.get_setting("smtp_from_name", "") or ENV_SMTP["from_name"],
        "use_tls": db.get_setting("smtp_use_tls", "1") == "1",
        "use_ssl": db.get_setting("smtp_use_ssl", "0") == "1",
        "timeout": ENV_SMTP["timeout"],
    }


def _connect_smtp():
    smtp = get_smtp_settings()
    if not smtp["host"] or not smtp["from_email"]:
        raise SmtpNotConfigured("SMTP sunucusu ve gonderen e-posta adresi zorunludur.")
    if smtp["use_tls"] and smtp["use_ssl"]:
        raise SmtpNotConfigured("STARTTLS ve dogrudan SSL ayni anda kullanilamaz.")
    auth_mode = smtp["auth_mode"]
    if auth_mode not in {"password", "relay"}:
        raise SmtpNotConfigured(f"Desteklenmeyen SMTP kimlik dogrulama modu: {auth_mode}")
    if auth_mode == "password" and (not smtp["username"] or not smtp["password"]):
        raise SmtpNotConfigured(
            "Uygulama sifresi ile giris modunda kullanici adi ve SMTP uygulama sifresi zorunludur."
        )
    tls_context = ssl.create_default_context()
    if smtp["use_ssl"]:
        server = smtplib.SMTP_SSL(
            smtp["host"], smtp["port"], timeout=smtp["timeout"], context=tls_context)
    else:
        server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=smtp["timeout"])
        server.ehlo()
        if smtp["use_tls"]:
            if not server.has_extn("starttls"):
                server.close()
                raise SmtpNotConfigured(
                    f"{smtp['host']} sunucusu STARTTLS destegi bildirmedi. Port/TLS ayarini kontrol edin.")
            server.starttls(context=tls_context)
            server.ehlo()
    if auth_mode == "password":
        password = smtp["password"]
        # Google uygulama sifresini arayuzde dortlu gruplar halinde gosterir.
        # Kullanici bosluklarla yapistirsa bile SMTP'ye 16 hane olarak gonder.
        if smtp["host"].lower() in {"smtp.gmail.com", "smtp.googlemail.com"}:
            password = "".join(password.split())
        try:
            server.login(smtp["username"].strip(), password)
        except smtplib.SMTPAuthenticationError as exc:
            try:
                server.quit()
            except Exception:
                server.close()
            if smtp["host"].lower() in {"smtp.gmail.com", "smtp.googlemail.com"}:
                credential_info = (
                    f"Google'a '{smtp['username'].strip()}' kullanicisi ve "
                    f"{len(password)} karakterlik parola gonderildi. "
                )
                if len(password) == 16:
                    credential_info += (
                        "Uygulama sifresi bicimi 16 karakter olarak dogru; ancak Google yine reddetti. "
                        "Sifre baska bir Google hesabinda uretilmis, iptal edilmis veya bu adres bir "
                        "takma ad/grup olabilir. Uygulama sifresinin olusturuldugu ANA Google hesabini "
                        "Kullanici adi alanina yazin ve o hesapta yeni bir uygulama sifresi olusturun. "
                        "Kurum hesabi bunu engelliyorsa programda 'Google Workspace SMTP Relay' ve "
                        "'IP yetkili relay' modunu secin; yoneticiniz tesisin sabit cikis IP'sini "
                        "Google Admin Console'da SMTP relay icin yetkilendirmelidir."
                    )
                else:
                    credential_info += (
                        "Google uygulama sifresi bosluklar cikarildiktan sonra tam 16 karakter olmalidir."
                    )
                raise SmtpAuthenticationFailed(
                    "Google hesabi girisi reddetti (535). " + credential_info
                ) from exc
            raise SmtpAuthenticationFailed(
                "SMTP sunucusu kullanici adi veya parolayi reddetti. Hesap adresini, "
                "uygulama sifresini ve kurumunuzun SMTP yetkisini kontrol edin."
            ) from exc
    return server


def _send_message(server, msg, smtp: dict):
    try:
        server.send_message(msg)
    except smtplib.SMTPResponseException as exc:
        detail = exc.smtp_error.decode("utf-8", errors="replace") \
            if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        if smtp["auth_mode"] == "relay":
            raise SmtpRelayRejected(
                f"Google Workspace SMTP relay mesaji reddetti ({exc.smtp_code}): {detail}. "
                "Google Admin Console > Gmail > Routing > SMTP relay service bolumunde "
                "bu bilgisayarin tesis sabit cikis IP'sini yetkilendirin ve gonderen alan adini "
                "example.com olarak sinirlayin."
            ) from exc
        raise


def send_daily_report(data: dict, recipients: list[str], cc: list[str],
                       attachments: list[str]) -> str:
    """Raporu gonderir; SMTP sunucu yanitini dondurur. Hata durumunda exception firlatir."""
    msg = EmailMessage()
    smtp = get_smtp_settings()
    tr_months = data["report_date"].split("-")
    subject_date = f"{tr_months[2]}.{tr_months[1]}.{tr_months[0]}"
    msg["Subject"] = f"Otopark Gunluk Giris-Cikis Raporu - {subject_date}"
    msg["From"] = formataddr((smtp["from_name"], smtp["from_email"]))
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.set_content("Bu rapor HTML destekleyen bir e-posta istemcisi gerektirir.")
    msg.add_alternative(_build_html(data), subtype="html")

    for path in attachments:
        if not path or not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            content = f.read()
        name = os.path.basename(path)
        if name.endswith(".xlsx"):
            maintype, subtype = "application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif name.endswith(".pdf"):
            maintype, subtype = "application", "pdf"
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)

    with _connect_smtp() as server:
        _send_message(server, msg, smtp)
    return "OK"


def send_test_email(recipient: str) -> str:
    """SMTP baglantisini dogrulayan kisa test mesaji. Hassas bilgi loglanmaz."""
    msg = EmailMessage()
    smtp = get_smtp_settings()
    msg["Subject"] = "Otopark Yonetim Sistemi - Test E-postasi"
    msg["From"] = formataddr((smtp["from_name"], smtp["from_email"]))
    msg["To"] = recipient
    msg.set_content("Bu bir test e-postasidir. SMTP ayarlariniz calisiyor.")
    with _connect_smtp() as server:
        _send_message(server, msg, smtp)
    return "OK"
