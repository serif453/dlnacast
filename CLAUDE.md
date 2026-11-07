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

Testler renderer, olay bildirimi, Chromecast, küçük resim, mpv ve güncelleyiciyi kapsıyor. Arayüz (`ui.py`) için otomatik test yok; onu elle denemek gerekir. Teşhis için `./tani.sh` var.

## Mimari (dlnacast/ paketi)
- `__init__.py` – `__version__` ve `REPO` (GitHub deposu).
- `__main__.py` – giriş noktası, `ui.App` başlatır.
- `guncelleme.py` – GitHub'daki `main` dalının sürümünü okur; arayüzdeki "Güncelle" düğmesi `kur.sh --guncelle --ac`'ı terminalde açar.
- `ui.py` – GTK4 arayüzü (libadwaita yok; görünüm dosyanın başındaki `CSS` ile, tema renklerinden türetilir). Başlık çubuğunun ortasında `Gtk.StackSwitcher` (`.sekmeler`) ile 4 sekme (`self.tabs`, son sekme config `sekme`'de): **yayinla** (`_build_cast_tab`: Medya kartı = kaynak `source_dd` dosya/URL, `cast_browse`/`add_url`/`clear_media`, oynatma listesi popover'ı; Oynatma kartı = kapak `_set_art`, `cast_clicked`, Durdur, önceki/sonraki, ses −/+ `change_volume`, Sessiz `on_mute_toggled`, Döngü `loop_switch` (config `dongu`) ve "Sıradakine geç" `autonext`; sağda Cihazlar kartı `cast_devs`, `_sync_cast_devices` ile `DevicePicker`'a bağlı), **alici** (`_build_receiver_tab`: `renderer_switch`, durum `_update_receiver_info` 2 sn'de bir, son olaylar `on_renderer_message`), **gez** (`_build_browse_tab`: üstte `DevicePicker` "Şurada oynat", solda `dev_list` ağ sunucuları + `local_list` bilgisayardaki klasörler, sağda `view_stack` karşılama/liste/yükleniyor, ızgara `Tile`/liste `Row`, altta `_build_player_bar`), **sunucu** (`_build_server_tab`: `server_switch` (config `sunucu`), `SharesPanel`, `_update_server_info`). Yayınla sekmesinde medya seçmek oynatmayı başlatmaz (`_pending`), "Yayınla" başlatır; Ağda Gez'de tıklamak hemen oynatır. İki sekmedeki ilerleme çubukları/etiketler `seek_scales`/`pos_labels`/`dur_labels`/`status_labels` listeleriyle birlikte güncellenir. Kuyruk (`QueueItem`, `set_queue`, `start_item`, `maybe_advance`), yoklama (`poll`/`on_status`), seek/ses ortak. Hatalar `friendly_error()` → `show_toast()` (tüm sekmelerin üstünde). Kısayollar `Gtk.ShortcutController` (CAPTURE; yazı kutusunda odak varken `_key` devre dışı), sürükle-bırak `on_drop` (Yayınla sekmesinde listeye koyar, başka sekmede oynatır).
  Arayüzü bulutta görmek için: `apt-get install gir1.2-gtk-4.0`, `/usr/bin/python3.12` ile venv, Xvfb altında sahte cihazlarla açıp `Gtk.WidgetPaintable` ile ekran görüntüsü alınabilir.
- `controlpoint.py` – `ControlPoint`: cihaz açıklamaları `TidyRequester` ile okunur (GET yanıtlarında etiket içi boşluklar `tidy_xml` ile kırpılır; bazı cihazlar adları satır sonuyla sarıyor, async-upnp-client bunu kaldıramıyor; test: `tests/test_controlpoint.py`, veri `tests/veri/vihome`). SSDP keşfi, ContentDirectory gezintisi, AVTransport/RenderingControl ile renderer kontrolü. Hedef türüne göre yönlendirir: mpv, Chromecast veya DLNA renderer.
- `mediaserver.py` – kendi UPnP MediaServer'ı (SSDP duyurusu + ContentDirectory SOAP + dosya akıtma). Sadece standart kütüphane.
- `localserver.py` – yerel dosyaları HTTP ile sunar (Range + CORS). `mediaserver` handler'ını buradan türetir. `lan_ip()` ve `guess_mime()` de burada.
- `localplayer.py` – "Bu bilgisayar" hedefi: mpv'yi `--idle --force-window=no` ile bir kez açar, IPC soketiyle yönetir. Müzik (`is_audio`: MIME ya da uzantı) penceresiz çalar (`force-window=no`, `vid=no`); video için her yüklemede `force-window=yes`, `vid=auto` yapılır. `ControlPoint.play` bu hedefe sadece http(s) ya da var olan yerel dosya gönderir (`check_local_uri`).
  Bulutta gerçek mpv denemesi: `apt-get install mpv x11-utils`, `MPV_HOME` içinde `ao=null`; Xvfb'de video çıkışı çalışmaz (eski kodda da), ama pencerenin açılıp açılmadığı `xwininfo -root -tree` ile görülür.
- `gomulu.py` – `EmbeddedPlayer`: alıcının program içi oynatıcısı. python-mpv (pip `mpv`, sistemdeki libmpv) ile `vo=libmpv`, görüntü `Gtk.GLArea`'ya OpenGL render API ile çizilir (`get_proc_address` önce EGL sonra GLX; FBO `glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING)`). `LocalPlayer` ile aynı arayüz; libmpv thread-safe olduğu için komutlar asyncio thread'inden doğrudan verilir. **Önemli:** render context (GLArea realize) loadfile'dan önce hazır olmalı, yoksa mpv video izini kapatır (`vid=False`); bu yüzden `prepare()`/`_ensure_ctx()` var ve pencere açılınca alan hazırlanır. Güvenlik: `ytdl=no`, betik/config yüklenmez. Arayüzde Alıcı sekmesindeki video kartı (`_build_video_card`, çubuk `_update_video_bar`, tam ekran `toggle_video_fullscreen`/`_apply_video_fs`: başlık çubuğu ve diğer kartlar gizlenir), ayar `alici_gomulu` (kapalıysa `LocalPlayer` ayrı pencere). python-mpv yoksa `gomulu.available()` False, eski davranış.
- `renderer.py` – `MediaRenderer`: DLNA alıcı (renderer). Ağdaki telefon/bilgisayar bu programı "TV" gibi görür; AVTransport + RenderingControl + ConnectionManager servislerini sunar, oynatmayı kendi `LocalPlayer` (ayrı mpv penceresi) ile yapar. Arayüzde başlık çubuğundaki "Alıcı" anahtarıyla açılır/kapanır, açık/kapalı durumu config'de saklanır.
- `cast.py` – `CastManager`: pychromecast ile keşif ve oynatma. Tek bir `CastBrowser` + `zeroconf` program boyunca açık kalır (yenilemede kapatılmaz); bulunan cihaza `cc.start()` ile hemen bağlanılır, bağlantılar yenilemede korunur. `_play_blocking` başarısız olursa `_reconnect` ile bir kez yeniden dener, sonra Türkçe `RuntimeError`. Cihaz IP'leri `known_hosts()` → config `cast_hosts` → `ControlPoint.cast_hosts` ile sonraki aramaya verilir. Testler sahte pychromecast ile: `tests/test_cast.py`.
- `kucukresim.py` – küçük resimler: `from_url` (sadece http/https, en fazla 8 MB), `from_local` (önce KDE/freedesktop önbelleği, yoksa `ffmpeg` ile kare/kapak; başarısızsa `.yok` işareti). Önbellek `~/.cache/dlnacast/kucukresim`. Arayüzde `ThreadPoolExecutor(3)` ile yüklenir, `_thumb_gen` klasör değişince eski işleri iptal eder. `Entry.thumb` DIDL'deki `upnp:albumArtURI` ya da image/* `<res>`'ten gelir.
- `olay.py` – UPnP olay bildirimi (GENA): `EventManager` abonelikleri tutar (`SUBSCRIBE`/yenileme/`UNSUBSCRIBE`), abone varken oynatıcıyı saniyede bir yoklar ve komutlardan sonra (`changed()`) hemen bakar; değişen değişkenleri AVT/RCS için `LastChange`, CMS için düz olarak `NOTIFY` ile gönderir. Yeni aboneye ~0.2 sn sonra tam durum (SEQ 0) gider. Güvenlik: geri çağrı adresi abonenin kendi IP'si olmalı, en fazla 32 abonelik. Renderer tarafı: `MediaRenderer._snapshot()`, `/event/avt|rcs|cms`. Testler: `tests/test_olay.py`.
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
- [x] Renderer olay bildirimi (UPnP eventing / `LastChange`), `olay.py`. async-upnp-client `DmrDevice` ile denendi; gerçek telefon uygulamasıyla henüz denenmedi
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
