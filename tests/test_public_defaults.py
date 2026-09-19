"""Publishing must not restore private operational data or enable real devices."""
from unittest.mock import patch


def test_fresh_install_has_no_device_credentials_or_endpoints(isolated_db):
    db = isolated_db
    db.ensure_standard_site_drivers()
    with db._connect() as connection:
        devices = connection.execute('SELECT * FROM devices').fetchall()
    assert len(devices) == 4
    for device in devices:
        assert not device['ip']
        assert not device['rtsp_url']
        assert not device['onvif_username']
        assert not device['onvif_password_enc']
    assert db.get_device_by_gate('CIKIS-1', 'BARRIER')['protocol'] == 'MOCK'
    for key in ['smtp_host', 'smtp_username', 'smtp_password_enc', 'smtp_from_email',
                'arma_barrier_ip', 'arma_led_ip', 'arma_barrier_trigger_hex']:
        assert db.get_setting(key) == ''
    for key in ['report_active', 'arma_led_enabled', 'arma_barrier_trigger_verified']:
        assert db.get_setting(key) == '0'


def test_reopening_does_not_restore_a_private_profile(isolated_db):
    db = isolated_db
    db.ensure_standard_site_drivers()
    device = db.get_device_by_gate('CIKIS-1', 'BARRIER')
    db.update_device(device['id'], ip='198.51.100.45', protocol='HTTP')
    db.set_setting('arma_led_ip', '198.51.100.46')
    db.init_db()
    db.ensure_standard_site_drivers()
    assert db.get_device_by_gate('CIKIS-1', 'BARRIER')['ip'] == '198.51.100.45'
    assert db.get_device_by_gate('CIKIS-1', 'BARRIER')['protocol'] == 'HTTP'
    assert db.get_setting('arma_led_ip') == '198.51.100.46'


def test_simulation_blocks_physical_led_even_if_enabled(isolated_db, monkeypatch):
    from app.hardware import DigitalDisplay
    monkeypatch.setattr('app.config.SIMULATION_MODE', True)
    isolated_db.set_setting('arma_led_enabled', '1')
    with patch('app.hardware.PhysicalLedQueue') as queue:
        display = DigitalDisplay()
        assert queue.call_args.kwargs['enabled'] is False
        display.reload_physical_settings()
        assert queue.call_args.kwargs['enabled'] is False
        display.close()
