# DLNA Cast

Linux için DLNA/UPnP programı: ağdaki sunucuları gezer, seçilen cihazda (TV, Chromecast, bu bilgisayar) oynatır ve kendi klasörlerini ağa paylaşır. Python + GTK4.

## Kullanıcı hakkında (önemli)
- Kullanıcı Türkçe yazar. Cevapları, açıklamaları, commit mesajlarını ve kod yorumlarını Türkçe yaz.
- Yazılım bilgisi sıfır, tam acemi. Her şeyi adım adım, sade dille anlat. Terminal komutlarını tam ve kopyalanabilir ver, ne işe yaradığını bir cümleyle açıkla.
- Sistem: CachyOS (Arch tabanlı), KDE Plasma, Wayland. Paket kurulumu `pacman` ile (`apt` değil).
- Kullanıcının kabuğu **fish**. Verdiğin komutlar fish'te çalışmalı: `<(...)`, `$(...)` gibi bash'e özel yazımlar kullanma; gerekirse `bash -c '...'` ya da önce dosyaya indirip `bash dosya.sh` kullan.
- Büyük değişiklikten önce ne yapacağını kısaca söyle, sonra yap. Kullanıcı kodu okuyup denetleyemez; bu yüzden değişiklikten sonra nasıl deneyeceğini de yaz.

## Çalıştırma
Kullanıcının bilgisayarında program `kur.sh` ile `~/.local/share/dlnacast` klasörüne kurulur; uygulama menüsünde "DLNA Cast" ve "DLNA Cast Güncelle" kısayolları vardır.

    ./baslat.sh                 # uygulamayı açar (.venv/bin/python -m dlnacast)
    ./tani.sh                   # teşhis aracı: paylaşım/keşif zincirini adım adım sınar
    bash kur.sh --yerel .       # bu klasördeki kodu kur (GitHub'a gitmeden dener)

Testler (GTK ve mpv gerekmez, sahte oynatıcı kullanır):
    python -m unittest discover -s tests -v

Şu an testler sadece renderer'ı kapsıyor. Arayüz (`ui.py`) için otomatik test yok; onu elle denemek gerekir. Teşhis için `./tani.sh` var.

## Mimari (dlnacast/ paketi)
- `__init__.py` – `__version__` ve `REPO` (GitHub deposu).
- `__main__.py` – giriş noktası, `ui.App` başlatır.
- `guncelleme.py` – GitHub'daki `main` dalının sürümünü okur; arayüzdeki "Güncelle" düğmesi `kur.sh --guncelle --ac`'ı terminalde açar.
- `ui.py` – GTK4 arayüzü (libadwaita yok; görünüm dosyanın başındaki `CSS` ile, tema renklerinden türetilir). Başlıkta hedef cihaz seçimi, yenile ve menü (Alıcı anahtarı, klasör paylaşma). Solda `local_list` (bu bilgisayar) ve `dev_list` (ağ sunucuları); sağda `view_stack` (karşılama / liste / yükleniyor); altta oynatıcı paneli (tek Oynat/Duraklat düğmesi `toggle_play`, durum `_set_state`). Kuyruk (`QueueItem`), otomatik sonraki parça, seek/ses burada.
  Arayüzü bulutta görmek için: `apt-get install gir1.2-gtk-4.0`, `/usr/bin/python3.12` ile venv, Xvfb altında sahte cihazlarla açıp `Gtk.WidgetPaintable` ile ekran görüntüsü alınabilir.
- `controlpoint.py` – `ControlPoint`: SSDP keşfi, ContentDirectory gezintisi, AVTransport/RenderingControl ile renderer kontrolü. Hedef türüne göre yönlendirir: mpv, Chromecast veya DLNA renderer.
- `mediaserver.py` – kendi UPnP MediaServer'ı (SSDP duyurusu + ContentDirectory SOAP + dosya akıtma). Sadece standart kütüphane.
- `localserver.py` – yerel dosyaları HTTP ile sunar (Range + CORS). `mediaserver` handler'ını buradan türetir. `lan_ip()` ve `guess_mime()` de burada.
- `localplayer.py` – "Bu bilgisayar" hedefi: mpv'yi `--idle` ile bir kez açar, IPC soketiyle yönetir.
- `renderer.py` – `MediaRenderer`: DLNA alıcı (renderer). Ağdaki telefon/bilgisayar bu programı "TV" gibi görür; AVTransport + RenderingControl + ConnectionManager servislerini sunar, oynatmayı kendi `LocalPlayer` (ayrı mpv penceresi) ile yapar. Arayüzde başlık çubuğundaki "Alıcı" anahtarıyla açılır/kapanır, açık/kapalı durumu config'de saklanır.
- `cast.py` – `CastManager`: pychromecast ile keşif ve oynatma. Engelleyici çağrılar thread havuzunda çalışır.
- `bridge.py` – `AsyncBridge`: asyncio döngüsünü ayrı thread'de çalıştırır, sonuçları `to_ui()` ile GTK ana thread'ine taşır.
- `config.py` – `~/.config/dlnacast/config.json` (uuid, paylaşılan klasörler).
- `tani.py` – teşhis betiği.

Paket dışı: `kur.sh` (kurulum/güncelleme/kaldırma, yedek alır ve hatada geri döner, menü kısayollarını yazar), `baslat.sh` (menüden açılır, hatayı `~/.cache/dlnacast/son-calisma.log`'a yazıp bildirim gösterir), `simge.svg`, `DEGISIKLIKLER.md`.

## Kritik kurallar
- **Thread ayrımı:** GTK widget'larına sadece ana thread'den dokun. Ağ işleri `AsyncBridge.submit(coro, on_done, on_error)` ile asyncio thread'inde yapılır. Başka thread'den arayüze dönmek için `to_ui(fn, *args)` kullan.
- `ControlPoint` metotları asyncio döngüsünde çalışır. Doğrudan UI thread'inden `await` etme.
- aiohttp oturumu çalışan döngüde oluşmalı (`_ensure()` bu yüzden tembel).
- Hedef kimlikleri: `LOCAL_PLAYER_ID = "player://this-computer"` (mpv), `LOCAL_ID = "local://this-computer"` (kendi paylaşım sunucumuz, SSDP'siz doğrudan okunur). Diğer hedefler cihazın `location` URL'sidir; Chromecast için `cast.has(key)` ile ayrılır.
- Bazı TV'ler oynatırken yeni adres kabul etmez; `play()` önce `Stop` gönderip durmasını bekler. Bu davranışı bozma.
- Yerel dosya mpv'ye doğrudan yol olarak verilir; başka hedeflere `LocalServer.add_file()` ile ağa açılıp URL verilir.
- Bir keşif kolu (Chromecast, SSDP) patlarsa diğerleri kaybolmaz (`return_exceptions=True`). Hatalar `ControlPoint.errors` listesine yazılır.
- **Renderer güvenliği:** `SetAVTransportURI` yalnızca `http://` ve `https://` kabul eder (yerel dosya yolu, `file://`, mpv'nin özel şemaları reddedilir, hata 716). SOAP gövdesi 1 MB ile sınırlı. Bu kontrolleri gevşetme. Alıcı varsayılan olarak kapalıdır, çünkü açıkken yerel ağdaki herkes bu bilgisayara oynatma emri gönderebilir (DLNA'nın doğası).
- Renderer'ın HTTP istekleri kendi thread'lerinde gelir; mpv işlerini `MediaRenderer._run()` ile asyncio döngüsüne aktarır. Arayüze bilgi vermek için `on_message` kullanılır (arayüz tarafı `to_ui` ile sarar).
- Renderer'ın mpv'si, uygulamanın "Bu bilgisayar (mpv)" hedefinden ayrıdır (`LocalPlayer(tag=...)` ile soket adı farklı). İkisini birleştirme.
- Yeni bağımlılık eklersen `requirements.txt`'e yaz; PyGObject/GTK4 sistem paketinden gelir, pip'ten değil.

## Kod stili
- Türkçe yorum ve arayüz metni. Docstring'ler kısa, tek satır.
- Tip ipuçları kullanılıyor (`typing`). Bilinçli geniş `except Exception` yerlerinde `# noqa: BLE001` var, tutarlı kal.
- Küçük, odaklı değişiklikler yap; çalışan kısmı gereksiz yere yeniden yazma.

## Yol haritası
- [x] SSDP keşfi, ContentDirectory gezintisi, DLNA renderer'da oynatma
- [x] Kendi media server'ı ve klasör paylaşımı
- [x] "Bu bilgisayar" (mpv) hedefi
- [x] Chromecast sender (pychromecast); Chromecast receiver kapsam dışı
- [x] DLNA renderer (bu bilgisayarı TV gibi gösterme), `renderer.py`
- [ ] Renderer olay bildirimi (UPnP eventing / `LastChange`): şu an abonelik kabul ediliyor ama olay gönderilmiyor. Çoğu uygulama konumu yoklayarak (polling) öğreniyor; bazıları (durum değişimini olayla bekleyenler) gecikmeli görebilir.
- [ ] Renderer: Chromecast receiver kapsam dışı; sıradaki parça (`SetNextAVTransportURI`) saklanıyor ama kullanılmıyor
- [ ] `tani.py`'ye renderer kontrolü ekle
- [ ] Alıcı için gerçek cihazlarla deneme (telefon uygulaması, Windows "Cihaza yayınla" vb.). Kodlama sırasında sadece sahte oynatıcıyla otomatik test yapıldı, gerçek mpv/ağ denenmedi


## Sürüm yayınlama (her değişiklikte)
Kaynak kod GitHub'da: https://github.com/serif453/dlnacast (public). Kullanıcı güncellemeleri tek tıkla buradan alır; eski `guncelleN.sh` / `gonder.sh` yöntemi kullanılmıyor.

1. Değişikliği yap, `python -m unittest discover -s tests -v` geçsin; mümkünse `bash kur.sh --yerel .` ile sahte bir HOME'da kurulumu dene.
2. `dlnacast/__init__.py` içindeki `__version__`'ı artır (hata düzeltme: 0.2.0 → 0.2.1, yeni özellik: 0.2.x → 0.3.0).
3. `DEGISIKLIKLER.md`'nin en üstüne yeni sürüm başlığı ve kullanıcının anlayacağı dille 1-4 madde ekle (güncelleyici bu bölümü kullanıcıya gösterir).
4. Commit (Türkçe mesaj, "DLNA Cast X.Y.Z: ..." ile başlasın) ve `main`'e gönder. Güncelleyici `main`'in son commit'indeki `__version__`'ı okur, kurulu sürümden büyükse o commit'i indirir. Bu yüzden `main`'e sürüm artırmadan gönderilen değişiklik güncelleme olarak kullanıcıya ulaşmaz (yeni kurulumlara ulaşır); `main`'i her zaman çalışır halde tut. (Bulut oturumundan etiket/tag gönderilemiyor, bu yüzden etiket kullanılmıyor.)
5. Kullanıcıya: programda "Güncelle" düğmesine basmasını ya da menüden "DLNA Cast Güncelle"yi açmasını ve neyi denemesi gerektiğini söyle.

`kur.sh`'ı değiştirirken dikkat: kullanıcının bilgisayarındaki güncelleyici, indirdiği yeni sürümün `kur.sh`'ını değil, kurulu olan eski `kur.sh`'ı çalıştırır. Dosya listesi (`DOSYALAR`) ve indirme mantığı geriye uyumlu kalmalı.
