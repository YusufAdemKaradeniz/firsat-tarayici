# Fırsat Tarayıcı — Kurulum Rehberi

Bu sistem 5 enstrümanı (EURUSD, GBPUSD, XAUUSD, US100, BTCUSD) **her 5 dakikada bir** senin stratejinle tarar ve fırsat çıkınca **Telegram'dan** telefonuna mesaj atar. GitHub'ın ücretsiz sunucusunda çalışır; bilgisayarın kapalı olabilir. Ücret yoktur.

Toplam süre: yaklaşık 20 dakika. Kurulum bilgisayardan (tarayıcıyla) yapılır, sonrası telefondan takip edilir.

---

## Elindeki dosyalar

| Dosya | Ne işe yarar |
| --- | --- |
| `scanner.py` | Stratejinin kendisi (tarayıcı kodu) |
| `requirements.txt` | Gerekli Python kütüphanelerinin listesi |
| `state.json` | Hangi bildirimin gönderildiğini hatırlar (aynı mesaj iki kez gelmesin diye) |
| `tarayici.yml.txt` | GitHub'a "her 5 dakikada bir çalış" diyen ayar dosyası |
| `KURULUM.md` | Bu rehber |

Zip dosyasını indirdiysen önce bir klasöre çıkar (sağ tık → Tümünü ayıkla).

---

## ADIM 1 — Telegram botunu oluştur (5 dk)

1. Telefonda Telegram'ı aç. Arama kısmına **@BotFather** yaz. Mavi tikli olanı aç.
2. **Başlat** (Start) de, sonra şunu yaz: `/newbot`
3. Botun adını sorar. Örnek: `Firsat Tarayici`
4. Kullanıcı adını sorar. Sonu **bot** ile bitmeli ve benzersiz olmalı. Örnek: `yusuf_firsat_bot`
5. BotFather sana şuna benzer bir **token** verir:
   `7412345678:AAHx9k2abcDEFghiJKLmnopQRstuVWxyz12`
   Bunu kopyala ve bir yere not et. **Bu token şifre gibidir, kimseyle paylaşma.**
6. BotFather'ın mesajındaki linke (`t.me/yusuf_firsat_bot`) tıkla, kendi botunu aç ve **Başlat** (Start) de. Sonra bota herhangi bir şey yaz, örneğin `merhaba`.
   Bu adım önemli: bota önce sen yazmazsan bot sana mesaj atamaz.

### Chat ID'ni bul

7. Bilgisayarda tarayıcıya şu adresi yaz; `TOKEN` yerine kendi token'ını koy (başındaki `bot` kelimesi kalacak):

   `https://api.telegram.org/botTOKEN/getUpdates`

   Örnek: `https://api.telegram.org/bot7412345678:AAHx9k2abc.../getUpdates`

8. Açılan sayfada `"chat":{"id":` yazan yeri bul. Yanındaki sayı senin **Chat ID**'n (örnek: `123456789`). Not et.
   - Sayfa `"result":[]` diye boş gelirse: bota Telegram'dan tekrar bir mesaj yaz, sayfayı yenile.
   - Alternatif: Telegram'da **@userinfobot**'a Başlat de, sana ID'ni söyler.

Artık elinde iki bilgi var: **token** ve **chat ID**.

---

## ADIM 2 — GitHub hesabı aç (3 dk)

1. **github.com** adresine git, sağ üstten **Sign up**.
2. E-posta, şifre ve kullanıcı adı gir, e-postana gelen kodu onayla.
3. Ücretsiz planı (Free) seç. Kredi kartı gerekmez.

---

## ADIM 3 — Depo (repository) oluştur (2 dk)

1. GitHub'da sağ üstteki **+** işaretine tıkla ve **New repository**'yi seç.
2. **Repository name:** `firsat-tarayici`
3. **Public** seçili olsun. Ücretsiz ve sınırsız çalışma süresi için bu gerekli. Kodun herkese açık olur ama token'ın ve chat ID'n gizli kalır (Adım 6'da gizli kasaya koyacağız).
4. "Add a README file" kutusunu **işaretleme**.
5. **Create repository**'ye bas.

---

## ADIM 4 — Dosyaları yükle (3 dk)

1. Açılan sayfada **"uploading an existing file"** yazan mavi linke tıkla.
2. Şu 4 dosyayı sürükleyip bırak ya da "choose your files" ile seç:
   - `scanner.py`
   - `requirements.txt`
   - `state.json`
   - `KURULUM.md`
3. Aşağıdaki yeşil **Commit changes** butonuna bas.

---

## ADIM 5 — Zamanlama dosyasını oluştur (3 dk)

Bu dosya gizli bir klasörde durmalı. O yüzden yükleme yerine elle oluşturuyoruz:

1. Deponun ana sayfasında **Add file** ve ardından **Create new file**'a tıkla.
2. Dosya adı kutusuna tam olarak şunu yaz:

   `.github/workflows/tarayici.yml`

   Her `/` yazdığında GitHub otomatik klasör oluşturur, bu normal.
3. `tarayici.yml.txt` dosyasını Not Defteri ile aç. **Ctrl+A** ile tamamını seç, **Ctrl+C** ile kopyala ve GitHub'daki büyük boş alana **Ctrl+V** ile yapıştır.
4. Sağ üstteki yeşil **Commit changes...** butonuna, açılan pencerede tekrar **Commit changes**'e bas.

---

## ADIM 6 — Token ve Chat ID'yi gizli kasaya koy (2 dk)

1. Deponun üstündeki **Settings** (dişli) sekmesine tıkla.
2. Soldaki menüden önce **Secrets and variables**'ı, sonra **Actions**'ı seç.
3. **New repository secret**'a tıkla:
   - **Name:** `TELEGRAM_TOKEN`
   - **Secret:** BotFather'ın verdiği token
   - **Add secret**
4. Tekrar **New repository secret**:
   - **Name:** `TELEGRAM_CHAT_ID`
   - **Secret:** chat ID sayın
   - **Add secret**

İsimleri büyük harfle, alt çizgiyle, tam olarak böyle yaz.

---

## ADIM 7 — Yazma iznini aç (1 dk)

1. Yine **Settings**'te kal. Soldan **Actions**'ı, sonra **General**'ı seç.
2. Sayfanın altındaki **Workflow permissions** bölümünde **Read and write permissions**'ı seç.
3. **Save**.

---

## ADIM 8 — İlk test (2 dk)

1. Deponun üstündeki **Actions** sekmesine tıkla.
   - "Workflows aren't being run on this repository" gibi bir uyarı çıkarsa yeşil **I understand my workflows, go ahead and enable them** butonuna bas.
2. Soldaki listeden **Firsat Tarayici**'yı seç.
3. Sağdaki **Run workflow**'a tıkla. Mod olarak **test**'i seç ve yeşil **Run workflow**'a bas.
4. 10–20 saniye içinde listede sarı bir daire çıkar. 1–2 dakika içinde yeşil tik olur.
5. Telegram'a iki mesaj gelir:
   - "Fırsat tarayıcı çalışıyor."
   - **Anlık durum**: her enstrüman için fiyat, 4s yön, en yakın talep ve arz bölgesi.

**Mesaj geldiyse kurulum bitti.** Artık sistem her 5 dakikada bir kendiliğinden çalışır, senin bir şey yapmana gerek yok. İlk otomatik çalışmanın başlaması 10–30 dakika sürebilir.

### Geçmiş test raporu (önerilir)

Aynı şekilde **Run workflow**'a tıkla, mod olarak **rapor**'u seç. Telegram'a son ~59 günde bu kurallarla kaç kurulum çıktığı, kaçının dolduğu ve 1:2 ile 1:3 hedeflerin sonuçları gelir. Bu, stratejinin gerçek veride ilk testi olur. Sonucu bana gönderirsen ayarları birlikte iyileştiririz.

---

## Telegram'a gelecek mesajlar

| Mesaj | Ne zaman | Ne yapmalısın |
| --- | --- | --- |
| **ALIŞ / SATIŞ KURULUMU** | 5 kapının hepsi açıldığında | Grafiği aç, bölgeyi ve MSS'i kendi gözünle kontrol et. Uygunsa verilen seviyeye limit emir koy. |
| **GİRİŞ SEVİYESİNE GELDİ** | Fiyat giriş seviyesine geri çekildiğinde | Limit emrin varsa dolmuştur. Yoksa fırsat hâlâ geçerli mi kontrol et. |
| **kurulum iptal** | Fiyat girişe gelmeden 1:2'ye gittiyse ya da 4 saat dolduysa | Bekleyen limit emrin varsa iptal et. |
| **Günlük özet** | Her sabah 08:00 (TR) sonrası ilk tarama | Günün planı: yön, yakın bölgeler, killzone saatleri. |
| **Uyarı: veri alınamadı** | Veri kaynağı geçici olarak cevap vermediyse (günde en fazla 1 kez) | Genelde kendiliğinden düzelir. |

Her kurulum mesajında: giriş (limit), stop, TP1 (1:2), TP2 (1:3), karşı bölgeye kalan mesafe ve bölgenin sınırları yazar.

---

## Önemli notlar

- **Altın ve Nasdaq fiyatları vadeli kontrattan gelir** (GC=F ve NQ=F). Broker'ındaki XAUUSD ve US100 fiyatı birkaç dolar/puan farklı olabilir. Seviyeleri birebir kopyalama. Mesajdaki bölgeyi kendi grafiğinde bul, emri kendi grafiğine göre koy. EURUSD, GBPUSD ve BTC'de fark çok küçüktür.
- **Veri birkaç dakika gecikmeli olabilir** (ücretsiz kaynak). 15 dakikalık strateji için genelde sorun olmaz.
- **GitHub zamanlaması tam dakikasında değildir.** Yoğun saatlerde 5 yerine 10–15 dakikada bir çalışabilir.
- **Haber takvimine bakmaz.** Kurulum mesajı gelince yüksek etkili veri (NFP, CPI, FOMC, ECB, BoE) var mı sen kontrol et.
- **Bu bir yatırım tavsiyesi değildir.** Sistem sadece kurallarına uyan durumları haber verir, işlem açmaz. Karar her zaman senin.

---

## Ayarları değiştirme

GitHub'da `scanner.py` dosyasına tıkla, sağ üstteki **kalem** simgesine bas. En üstteki **AYARLAR** bölümünü düzenle ve **Commit changes**'e bas.

| Ayar | Ne yapar | Varsayılan |
| --- | --- | --- |
| `use_bias` | 4s yöne ters kurulumları eler | True |
| `use_killzone` | Sadece Londra ve NY AM saatlerinde kurulum arar | True |
| `require_sweep` | Likidite süpürmesi şart | True |
| `disp_mult` | Bölge çıkış mumunun ne kadar güçlü olması gerektiği (büyük = daha az ama daha kaliteli bölge) | 1.2 |
| `mss_mult` | Yapı kırılım mumunun gücü | 0.8 |
| `watch_bars` | Bölgeye dokunduktan sonra MSS için kaç mum beklenir | 24 (6 saat) |
| `arm_bars` | Kurulumdan sonra girişe gelmesi için kaç mum beklenir | 16 (4 saat) |
| `min_rr` | Karşı bölgeye en az kaç R yer olmalı | 2.0 |
| `DAILY_BRIEF_TR_HOUR` | Günlük özet saati (Türkiye) | 8 |
| `SEND_CANCEL_ALERTS` | İptal mesajları gelsin mi | True |

Enstrüman eklemek ya da çıkarmak için `SYMBOLS` listesini düzenle. Kodlar Yahoo Finance'teki koddur, örneğin `USDJPY=X`, `ES=F`, `ETH-USD`.

Sistemi **durdurmak** için: Actions sekmesinde **Firsat Tarayici**'yı seç, sağ üstteki **...** menüsünden **Disable workflow**'a bas. Tekrar başlatmak için **Enable workflow**.

---

## Sorun giderme

| Sorun | Çözüm |
| --- | --- |
| Test çalıştı (yeşil tik) ama Telegram'a mesaj gelmedi | Secret isimlerini kontrol et (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`). Bota Telegram'dan en az bir kez mesaj yazdığından emin ol. Chat ID'yi tekrar kontrol et. |
| Actions'ta kırmızı çarpı var | Kırmızı satıra, sonra **tara**'ya tıkla. Kırmızı yazan adımı aç, ekran görüntüsünü bana gönder. |
| "Durumu kaydet" adımında "Permission denied" / 403 | Adım 7'yi yap (Read and write permissions). |
| "Run workflow" butonu görünmüyor | `.github/workflows/tarayici.yml` dosyasının adı ve yeri tam doğru mu kontrol et (Adım 5). |
| "veri alınamadı" uyarısı sık geliyor | Yahoo geçici olarak engelliyor olabilir, genelde birkaç saatte düzelir. Sürerse bana yaz, farklı veri kaynağı ekleriz. |
| Hiç kurulum mesajı gelmiyor | Normal olabilir: 5 kapının hepsi nadiren aynı anda açılır. **rapor** moduyla geçmişte kaç kurulum çıktığına bak. Çok azsa ayarları gevşetiriz. |
| 60 gün sonra durdu | GitHub, hiç değişiklik olmayan depolarda zamanlamayı kapatır. Günlük özet her gün `state.json`'u güncellediği için bu olmamalı. Olursa Actions'tan **Enable workflow** de. |
