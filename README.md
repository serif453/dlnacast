# DLNA Cast

Linux için DLNA/UPnP programı: ağdaki sunucuları gezer, seçilen cihazda (TV, Chromecast, bu bilgisayar) oynatır, kendi klasörlerini ağa paylaşır ve bu bilgisayarı ağdaki telefon/bilgisayarlara "TV" olarak gösterir.

## Kurulum (tek komut)
Terminali aç (Konsole), şu satırı yapıştır ve Enter'a bas:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/serif453/dlnacast/main/kur.sh)
```

Eksik sistem paketi (GTK4, PyGObject, mpv) varsa şifren istenir ve kurulur. Bitince program uygulama menüsünde **DLNA Cast** adıyla görünür; ondan sonra hep tıklayarak açarsın.

Program `~/.local/share/dlnacast` klasörüne kurulur. Ayarlar `~/.config/dlnacast` klasöründe durur.

## Güncelleme (tek tık)
- Yeni sürüm çıkınca programın başlık çubuğunda **Güncelle** düğmesi belirir. Tıkla: program kapanır, güncellenir ve yeniden açılır.
- Ya da uygulama menüsünden **DLNA Cast Güncelle**'yi aç.

Güncelleme önce yedek alır. Bir şey ters giderse kendiliğinden önceki sürüme döner. Son 3 yedek `~/.local/share/dlnacast/yedekler` klasöründe durur.

## Kaldırma
```bash
bash ~/.local/share/dlnacast/kur.sh --kaldir
```

## Özellikler
- SSDP keşfi (MediaServer + MediaRenderer)
- ContentDirectory gezintisi
- Seçilen renderer'da oynatma (AVTransport), kuyruk, otomatik sonraki
- Chromecast sender (pychromecast)
- Bu bilgisayarda oynatma (mpv)
- Media server: klasör paylaşımı
- Alıcı (renderer): başlık çubuğundaki "Alıcı" anahtarıyla açılır; telefon/bilgisayardan gönderilen video ve müzik bu bilgisayarda mpv ile oynar

## Alıcı nasıl kullanılır
1. Programı aç, başlık çubuğunda "Alıcı" anahtarını aç.
2. Telefondaki DLNA uygulamasında (BubbleUPnP, VLC, Kodi, Windows "Cihaza yayınla" vb.) cihaz listesinde "DLNA Cast Alıcı (bilgisayar adı)" görünür.
3. Oradan bir video seçip gönder; mpv penceresi açılıp oynatır.

Cihaz listede görünmüyorsa güvenlik duvarını kontrol et (UDP 1900 ve programın açtığı TCP port). Teşhis için: `~/.local/share/dlnacast/tani.sh`

## Geliştirme
    python -m venv --system-site-packages .venv
    .venv/bin/pip install -r requirements.txt
    ./baslat.sh                                  # kaynaktan çalıştır
    python -m unittest discover -s tests -v      # testler
    bash kur.sh --yerel .                        # bu klasördeki kodu kur (GitHub'a gitmeden)
