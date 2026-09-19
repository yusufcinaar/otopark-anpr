# Ayrıntılı kurulum notları

Programı ilk kez denemek için README'deki kurulum adımlarını kullanabilirsin. Aşağıdaki bilgiler kendi cihazlarını bağlamak veya kod üzerinde çalışmak isteyenler için.

## Elle kurulum

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m app.main
```

## Gerçek cihazları bağlama

1. `.env` içindeki `SIMULATION_MODE=false` değerini ayarlayıp uygulamayı yeniden başlatın.
2. **Tanımlar → Kamera Tanımları** üzerinden kendi IP, ONVIF/RTSP ve hesap bilgilerinizi girin.
3. Bariyer ekranında kendi cihazınızın protokolünü, adresini ve üretici komutlarını yapılandırın.
4. LED tabelayı kendi adresinizle etkinleştirin.
5. SMTP ve rapor alıcılarını kendi hesabınızla yapılandırıp test gönderimi yapın; ardından zamanlamayı etkinleştirin.

Webhook varsayılan olarak yalnız `127.0.0.1:8090` üzerinde dinler.
LAN kameraları için `WEBHOOK_HOST` değerini kendi ağınıza göre değiştirin.
Webhook'ta yerleşik kimlik doğrulama bulunmaz; internetten erişime açmayın,
güvenlik duvarında yalnız yetkili kamera adreslerine izin verin.

Metcom OUT3 örnek komutları her cihaz için genel değildir. Uyumlu donanımda doğruladıktan
sonra `.env` içindeki `METCOM_PROFILE_HOST` alanına kendi cihaz adresinizi girin ve
arayüzde komut doğrulamasını tamamlayın. Yayın sürümü bu komutları kendiliğinden etkinleştirmez.
Bilinmeyen fiziksel kapatma komutu başarılıymış gibi raporlanmaz.

## Veri ve gizlilik

- Kalıcı veriler: `%LOCALAPPDATA%\OtoparkANPRPublic\data`
- Kurulum ortamı: `%LOCALAPPDATA%\OtoparkANPRPublic\runtime`
- Farklı bir veri klasörü için uygulamayı başlatmadan önce `OTOPARK_DATA_DIR` ortam değişkenini ayarlayın.
- `.env`, veritabanları, fotoğraf kayıtları, raporlar, loglar, yedekler ve paketler Git dışında tutulur.
- Örneklerdeki `192.0.2.x` ve `198.51.100.x` adresleri temsili adreslerdir.
- Kamera/SMTP parolaları Windows DPAPI ile saklanır; başka Windows hesabına taşındığında yeniden girilmelidir.

## Testler

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

Testler geçici veritabanları ve taklit donanım kullanır. Fiziksel kamera, bariyer ve
SMTP doğrulaması her kurulumda ayrıca yapılmalıdır. Python testleri Windows üzerinde çalıştırılır.

## Lisans notu

Bu proje için ayrıca bir açık kaynak lisansı seçilmedi. Kullanılan kütüphanelerin ve plaka okuma modellerinin kendi lisansları var.
