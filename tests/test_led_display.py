from unittest.mock import patch, MagicMock

from app.led_display import HuiduMonoLed, render_mono_bitmap


def test_display_only_shows_plate_and_fee_during_payment():
    from app.hardware import DigitalDisplay

    with patch("app.hardware.PhysicalLedQueue") as queue:
        display = DigitalDisplay()
        messages = []
        display.content_changed.connect(messages.append)
        for action in (display.show_idle, display.show_processing,
                       lambda: display.show_paid("34ABC123"),
                       lambda: display.show_error("GIRIS KAYDI BULUNAMADI")):
            display.show_fee("34ABC123", "1 saat", 350, "TL")
            queue.return_value.submit.assert_called_with(["34 ABC 123", "350 TL"])
            assert messages[-1]["plate"] == "34 ABC 123"
            action()
            queue.return_value.submit.assert_called_with(["DEMO", "OTOPARK"])
            assert messages[-1] == {"mode": "idle", "text": "DEMO OTOPARK"}
        display.close()


def test_entry_does_not_replace_exit_payment_display():
    from app.services.parking_service import ParkingService
    from conftest import make_event

    with patch("app.hardware.PhysicalLedQueue") as queue:
        service = ParkingService()
        queue.return_value.submit.assert_called_with(["DEMO", "OTOPARK"])
        queue.return_value.submit.reset_mock()
        service.ingest_camera_event(make_event(plate="34ABC123"))
        queue.return_value.submit.assert_not_called()
        service.exit_display.show_fee("34ABC123", "1 saat", 350, "TL")
        queue.return_value.submit.reset_mock()
        service.ingest_camera_event(make_event(plate="34DEF456"))
        queue.return_value.submit.assert_not_called()
        service.exit_display.close()


def test_64x32_bitmap_256_bayt_uretilir():
    bitmap = render_mono_bitmap(["34 ABC 123", "350 TL"])
    assert len(bitmap) == 256
    assert any(bitmap)


def test_led_cerceveleri_saha_boyutlari_ve_checksum():
    led = HuiduMonoLed()
    frames = led._transaction_frames(render_mono_bitmap(["DEMO", "OTOPARK"]))
    assert [len(x) for x in frames] == [77, 343, 33]
    for packet in frames:
        assert packet[:2] == b"HT"
        assert packet[-1:] == b"\xaa"
        assert int.from_bytes(packet[-3:-1], "big") == sum(packet[:-3]) & 0xFFFF


def test_led_uc_paketi_sirali_gonderir():
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.recv.return_value = b"HR" + bytes(29)
    with patch("app.led_display.socket.create_connection", return_value=sock):
        HuiduMonoLed().show_lines(["34 ABC 123", "350 TL"])
    assert sock.sendall.call_count == 3
