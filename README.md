# DLNA Cast

Linux için DLNA/UPnP programı: ağdaki sunucuları gezer, seçilen cihazda (TV, Chromecast, bu bilgisayar) oynatır, kendi klasörlerini ağa paylaşır ve bu bilgisayarı ağdaki telefon/bilgisayarlara "TV" olarak gösterir.

## Kurulum (CachyOS / Arch)
    sudo pacman -S python-gobject gtk4 mpv
    python -m venv --system-site-packages .venv && . .venv/bin/activate
    pip install -r requirements.txt
    ./baslat.sh

Debian/Ubuntu için: `sudo apt install python3-gi gir1.2-gtk-4.0 mpv`

## Özellikler
- [x] SSDP keşfi (MediaServer + MediaRenderer)
- [x] ContentDirectory gezintisi
- [x] Seçilen renderer'da oynatma (AVTransport), kuyruk, otomatik sonraki
- [x] Chromecast sender (pychromecast)
- [x] Bu bilgisayarda oynatma (mpv)
- [x] Media server: klasör paylaşımı
- [x] Renderer (alıcı): başlık çubuğundaki "Alıcı" anahtarıyla açılır; telefon/bilgisayardan gönderilen video ve müzik bu bilgisayarda mpv ile oynar

## Alıcı (renderer) nasıl kullanılır
1. Uygulamayı aç, başlık çubuğunda "Alıcı" anahtarını aç.
2. Telefondaki DLNA uygulamasında (BubbleUPnP, VLC, Kodi, Windows "Cihaza yayınla" vb.) cihaz listesinde "DLNA Cast Alıcı (bilgisayar adı)" görünür.
3. Oradan bir video seçip gönder; mpv penceresi açılıp oynatır.

Cihaz listede görünmüyorsa güvenlik duvarını kontrol et (UDP 1900 ve uygulamanın açtığı TCP port).

## Çalıştırılabilir paket
`bash gonder.sh` iki arşiv üretir: `~/dlnacast.tar.gz` (Claude'a yüklemek için) ve `~/dlnacast-calistir.tar.gz` (çalıştırmak için). İkincisini açıp `./calistir.sh` çalıştırman yeter; ilk açılışta kendi `.venv`'ini kurar (GTK4/PyGObject ve mpv eksikse `calistir.sh` bunu söyler ve `sudo` şifresiyle kurmayı dener; pacman, apt ve dnf desteklenir).

## Test
    python -m unittest discover -s tests -v
