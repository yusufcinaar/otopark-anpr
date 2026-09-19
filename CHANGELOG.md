# Değişiklik geçmişi

## 0.1.0 — İlk genel dağıtım

Mevcut masaüstü uygulamasının tek bir başlangıç sürümü olarak yayımlanması.
Aşağıdaki aşamalar önceki çalışmaların özetidir, ayrı Git sürümleri değildir.

1. **Temel akış:** plaka normalizasyonu, kamera olayları, SQLite kayıtları, giriş–çıkış eşleştirme.
2. **İşletme işlevleri:** tarife ve ödeme, abonelik, kullanıcı rolleri, kara liste ve işlem kayıtları.
3. **Entegrasyonlar:** RTSP/ONVIF, bariyer ve LED sürücüleri, SMTP ve Excel/PDF raporları.
4. **Kurulum ve süreklilik:** otomatik Python/bağımlılık kurulumu, kalıcı veri dizini, yedekleme ve tek uygulama oturumu.
5. **Operasyon iyileştirmeleri:** serbest geçiş, peş peşe araç olayları, kasiyer yetkileri, okunaklı plaka biçimi.
6. **Görsel inceleme:** kamera, ödeme ve rapor fotoğraflarında büyütme, yakınlaştırma ve sürükleme.

### Yayın hazırlığı

- Kuruma özel adres, hesap ve varsayılan cihaz parolaları kaldırıldı.
- Otomatik saha profili geçişleri genel dağıtımda devre dışı bırakıldı.
- Simülasyon varsayılan yapıldı; fiziksel LED ve otomatik raporlama kapatıldı.
- Webhook varsayılanı yerel bilgisayarla sınırlandırıldı.
- Public sürüm için bağımsız veri ve çalışma ortamı dizini ayrıldı.
- Sentetik verilerle ekran görüntüleri ve yeniden üretim aracı eklendi.
