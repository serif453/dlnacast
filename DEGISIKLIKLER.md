# Değişiklikler

## 0.9.0
- Alıcı: telefondan gönderilen video artık ayrı pencerede değil, programın "Alıcı" sekmesinde oynar. Video gelince sekme kendiliğinden açılır.
- Tam ekran: videonun altındaki ⛶ düğmesi, F tuşu ya da videoya çift tıklama. Çıkmak için Esc.
- Oynatıcının altında oynat/duraklat, 10 sn ileri/geri, ilerleme ve ses çubuğu var; Boşluk ve ← → tuşları da çalışır.
- Eski usul ayrı pencere istersen "Program içinde oynat" anahtarını kapat.

## 0.8.1
- Düzeltme: bazı telefon ve kutuların medya sunucusu (örn. Honor "VihomeMediaServer") "Ağda Gez" listesinde hiç çıkmıyordu; artık çıkıyor.

## 0.8.0
- Yeni görünüm: üstte dört sekme var: Yayınla, Alıcı, Ağda Gez, Sunucu.
- Yayınla: bir video ya da müzik seç (ya da internet adresi yapıştır), sağdan TV'yi seç, "Yayınla"ya bas. Ses −/+, Sessiz, Döngü ve "Sıradakine geç" burada.
- Alıcı ve Sunucu artık kendi sekmelerinde: açık mı kapalı mı, ağda hangi adla göründüğü ve son olaylar tek bakışta görülür.
- Ağda Gez: ağdaki sunucularda ve bilgisayarındaki klasörlerde eskisi gibi küçük resimlerle gezersin.

## 0.7.0
- Alıcı artık telefona anında haber verir: oynatma, duraklatma, durma, parçanın bitmesi ve ses değişikliği telefondaki uygulamada (BubbleUPnP, Kodi vb.) hemen görünür.
- Telefon uygulamasının çubuğu ve düğmeleri bilgisayardaki oynatmayla daha uyumlu çalışır.
- Güvenlik: bu bildirimler sadece abone olan cihazın kendisine gönderilir.

## 0.6.0
- Klasörler ve sunucular küçük resimli ızgara olarak açılır: albüm kapakları, sunucunun küçük resimleri, bilgisayardaki videolardan bir kare. Sağ üstteki düğmeyle listeye geçebilirsin.
- Chromecast daha güvenilir: TV bulunur bulunmaz bağlanılır, bağlantı yenilemede kopmaz, olmazsa kendiliğinden bir kez yeniden bağlanır.
- TV'nin ağ adresi hatırlanır; arama duyuruyu kaçırsa bile TV listede çıkar.

## 0.5.0
- Müzik artık program içinde çalar: "Bu bilgisayar" seçiliyken mp3, flac gibi dosyalar ayrı pencere açmadan çalar; alttaki panelden yönetilir.
- Videolar eskisi gibi kendi penceresinde açılır.
- Güvenlik: ağdaki sunuculardan gelen adresler bu bilgisayarda çalınmadan önce denetlenir.

## 0.4.1
- Düzeltme: çalan bir şey yokken ok tuşlarına basınca "Failed to execute seek" hatası çıkıyordu; artık sadece hatırlatma yapılır.
- Ok tuşuna art arda basınca tek bir sarma komutu gönderilir; ses çubuğu ok tuşlarıyla yine ayarlanabilir.
- Boşluk tuşu: kuyruk boşsa "önce video seç" der, durmuş videoyu yeniden başlatır.

## 0.4.0
- Başlığın ortasında büyük "Şurada oynat" düğmesi: cihazlar simgeleriyle (TV, Chromecast, bu bilgisayar) listelenir.
- Hatalar ekranda sade Türkçe bildirim olarak çıkar; açtığın klasörler artık hatırlanır (✕ ile listeden kaldırılır).
- Videoların yanında ▶ düğmesi, çalan video listede vurgulu; dosya ya da klasörü pencereye sürükleyip bırakabilirsin.
- Kısayollar: Boşluk oynat/duraklat, ← → 10 sn, Backspace geri. "Bitince sıradakine geç" ayarı menüde.

## 0.3.0
- Yeni, sade arayüz: solda "Dosya aç", "Klasör aç", bilgisayarındaki klasörler ve ağdaki sunucular.
- Boş ekranda yol gösteren karşılama sayfası; listelerde klasör, video ve müzik simgeleri.
- Altta oynatıcı paneli: çalan parçanın adı, hangi cihazda çaldığı ve tek büyük Oynat/Duraklat düğmesi.
- Alıcı modu ve klasör paylaşma sağ üstteki menüde (☰); alıcı açıkken başlıkta "Alıcı açık" yazar.

## 0.2.0
- Tek komutla kurulum: program uygulama menüsünde "DLNA Cast" olarak görünür, tıklayarak açılır.
- Tek tıkla güncelleme: yeni sürüm varsa başlık çubuğunda "Güncelle" düğmesi çıkar; menüde "DLNA Cast Güncelle" de var.
- Güncelleme önce yedek alır, hata olursa önceki sürüme döner.
- Pencere başlığında sürüm numarası görünür.

## 0.1.0
- İlk sürüm: DLNA sunucu gezme, TV/Chromecast/bu bilgisayarda oynatma, klasör paylaşma, alıcı (renderer).
