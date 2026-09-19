# Araç Plaka Tanıma ve Otopark Yönetimi

Bu projeyi, otoparka giren ve çıkan araçları takip etmek ve park ücretini hesaplamak için geliştirdim. Windows üzerinde çalışan, Türkçe arayüzü olan bir masaüstü programı.

Araç geldiğinde kamera plakasını okuyor ve giriş saatini kaydediyor. Çıkışta da aynı plakayı bulup aracın ne kadar kaldığını ve ödeyeceği tutarı gösteriyor. Kamera görüntüleri, ödeme, aboneler ve raporlar aynı programın içinde bulunuyor.

![Programın ana ekranı](docs/images/overview.png)

**Buradaki ekran görüntüleri örnek verilerle hazırlandı.** Araç çizimleri ve plakalar demo amaçlı; gerçek araç fotoğrafları veya işletme kayıtları değil.

## Programda neler var?

- **Plaka okuma:** İki giriş ve bir çıkış kamerası için ayrı alanlar var. Okunan plakayı ve araç görüntüsünü ekranda görebiliyorsun.
- **Giriş ve çıkış takibi:** Hangi araç içeride, ne zaman girmiş, ne zaman çıkmış; kayıtlarından bakabiliyorsun.
- **Ücret ve ödeme:** Belirlenen tarifeye göre park ücretini hesaplıyor. Nakit ödeme alındığında para üstünü de gösteriyor.
- **Aboneler:** Sürekli gelen araçları abone olarak ekleyebiliyorsun. Excel'den abone listesi aktarma da var.
- **Bariyer ve tabela:** Uyumlu cihazlar bağlandığında çıkış bariyerini kontrol edebiliyor. Ödeme sırasında tabelada plaka ve tutar gösteriliyor.
- **Serbest geçiş:** Ücret alınmadan geçiş yapılabiliyor; araçların giriş ve çıkış kayıtları tutulmaya devam ediyor.
- **Raporlar:** Plakayla eski kayıtları arayabiliyor, Excel/PDF raporu alabiliyor ve e-posta raporlarını ayarlayabiliyorsun.
- **Fotoğraf büyütme:** Giriş, çıkış, ödeme ve rapor ekranındaki fotoğraflara tıklayıp yakından bakabiliyorsun.
- **Kullanıcı hesapları:** Yönetici ve kasiyer gibi farklı yetkilerle kullanılabiliyor.

## Nasıl kurulur?

1. Bu sayfanın üstündeki **Code → Download ZIP** ile projeyi indir.
2. ZIP dosyasını bir klasöre çıkar. Dosyaları ZIP'in içinden çalıştırma.
3. **`kur.bat`** dosyasına çift tıkla. Gerekli Python sürümünü, kütüphaneleri ve plaka okuma modellerini hazırlıyor. İlk kurulumda internet bağlantısı gerekiyor.
4. Kurulum bittikten sonra **`run.bat`** dosyasını aç.

İlk giriş bilgileri:

| Kullanıcı adı | Şifre |
| --- | --- |
| `admin` | `admin` |

Kendi kullanımına geçerken bu şifreyi kullanıcı ayarlarından değiştir.

Şu an ayrı bir `.exe` dosyası yok. Programı `run.bat` ile açıyorsun.

## Kamera olmadan deneyebilir miyim?

Evet. GitHub'daki sürüm ilk açılışta **simülasyon modunda** çalışıyor. Yani gerçek kamera veya bariyer bağlamadan örnek araçlarla programı deneyebiliyorsun. Kamera alanlarındaki **Girişi Simüle Et** ve **Çıkışı Simüle Et** düğmeleri bunun için var.

Gerçek cihazlarla kullanmak istersen kendi kamera, bariyer ve tabela bilgilerini girmen gerekiyor. E-posta göndermek için de kendi mail ayarlarını eklemelisin. Bunların ayrıntılarını [kurulum notlarına](docs/KURULUM.md) yazdım.

## Kayıtları nasıl görüyorum?

Raporlar ekranında plaka arayarak aracın giriş ve çıkış saatlerini, içeride kaldığı süreyi ve kayıtlı fotoğraflarını görebiliyorsun.

![Araç kayıtları ve rapor ekranı](docs/images/reports.png)

## Kod tarafı

Program Python ile yazıldı. Arayüzde **PySide6**, plaka okumada **FastALPR / ONNX**, kayıtları saklamak için **SQLite** kullanılıyor.

Kodları incelemek istersen ana bölümler şöyle:

- `app/ui`: Ekranlar ve düğmeler.
- `app/anpr`: Görüntüden plaka okuma kısmı.
- `app/services`: Giriş, çıkış, ücret ve ödeme işlemleri.
- `app/reports`: Rapor hazırlama ve e-posta işlemleri.
- `tests`: Programın farklı işlevlerini kontrol eden testler.

Elle kurulum ve test komutları da [kurulum notlarında](docs/KURULUM.md) var. Testlerin geçmesi, her kamera veya bariyerin doğrudan çalışacağı anlamına gelmiyor; bağlı cihazla ayrıca denemek gerekiyor.

İlk yüklemede projenin güncel hâlini paylaştım. Şimdiye kadar eklenenleri [değişiklik geçmişinde](CHANGELOG.md) topladım.
