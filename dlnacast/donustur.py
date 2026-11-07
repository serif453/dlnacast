"""Dönüştürme (transcode): cihazın açamadığı dosyayı ffmpeg ile anında uygun biçime çevirir.

İki yol var: "kap" (sadece kabı değiştirir, görüntüye dokunmaz, işlemciyi yormaz) ve
"tam" (görüntüyü H.264'e, sesi AAC/MP3'e yeniden kodlar). Hangisinin gerektiğine
ffprobe ile dosyanın içine bakılarak karar verilir.
"""
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from xml.sax.saxutils import escape, quoteattr

# Hedef türleri
DLNA = "dlna"
CHROMECAST = "chromecast"

# Yöntemler
DOGRUDAN = "dogrudan"  # dönüştürme gerekmez, dosya olduğu gibi gider
KAP = "kap"            # görüntü kopyalanır, sadece kap (ve gerekirse ses) değişir
TAM = "tam"            # görüntü yeniden kodlanır

# Görüntüyü 1080p'yi aşmayacak, kenarları çift sayı olacak şekilde küçültür.
_OLCEK = ("scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,"
          "scale=trunc(iw/2)*2:trunc(ih/2)*2")

# Chromecast'in kendisi oynatabildikleri (en eski Chromecast'ler de dahil, güvenli liste)
_CC_VIDEO = {"h264", "vp8", "vp9"}
_CC_SES = {"aac", "mp3", "opus", "vorbis", "flac"}
_CC_KAP = {"mp4", "mov", "webm"}  # MKV'yi Chromecast güvenilir açamıyor
_CC_SADECE_SES = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le"}

# MPEG-TS içine kopyalanabilecek sesler (TV'lerin çoğu açar)
_TS_SES = {"aac", "mp3", "ac3"}
# MP4 içine kopyalanabilecek sesler
_MP4_SES = {"aac", "mp3"}


@dataclass
class Bilgi:
    """ffprobe'dan okunan özet."""
    video: str = ""       # görüntü kodeki (h264, hevc...) yoksa ""
    piksel: str = ""      # yuv420p, yuv420p10le...
    ses: str = ""         # ses kodeki
    kap: str = ""         # "matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2"...
    sure: float = 0.0     # saniye, bilinmiyorsa 0


@dataclass
class Plan:
    """Bir dosyanın nasıl gönderileceği."""
    yontem: str           # DOGRUDAN / KAP / TAM
    hedef: str            # DLNA / CHROMECAST
    mime: str             # cihaza söylenecek tür
    video_kopya: bool = False
    ses_kopya: bool = False
    sadece_ses: bool = False

    def aciklama(self) -> str:
        if self.yontem == KAP:
            return "kabı değiştirilerek"
        if self.yontem == TAM:
            return "dönüştürülerek (MP3)" if self.sadece_ses else "dönüştürülerek (H.264)"
        return ""


def ffmpeg_yolu() -> Optional[str]:
    return shutil.which("ffmpeg")


def kullanilabilir() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def incele(kaynak: str, zaman_asimi: float = 15.0) -> Bilgi:
    """Dosyanın (ya da http adresinin) içeriğini ffprobe ile okur. Hata olursa RuntimeError."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe bulunamadı (ffmpeg paketi kurulu değil).")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", kaynak],
            capture_output=True, timeout=zaman_asimi, check=True).stdout
        veri = json.loads(out or b"{}")
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise RuntimeError(f"Dosya incelenemedi: {exc}") from exc
    return bilgi_oku(veri)


def bilgi_oku(veri: dict) -> Bilgi:
    """ffprobe JSON çıktısından Bilgi üretir (testlerde doğrudan kullanılır)."""
    b = Bilgi()
    for s in veri.get("streams", []):
        tur = s.get("codec_type")
        kapak = (s.get("disposition") or {}).get("attached_pic") == 1  # albüm kapağı, video değil
        if tur == "video" and not kapak and not b.video:
            b.video = s.get("codec_name", "")
            b.piksel = s.get("pix_fmt", "")
        elif tur == "audio" and not b.ses:
            b.ses = s.get("codec_name", "")
    fmt = veri.get("format", {})
    b.kap = fmt.get("format_name", "")
    try:
        b.sure = float(fmt.get("duration") or 0)
    except ValueError:
        b.sure = 0.0
    return b


def _kap_icinde(kap: str, liste: set) -> bool:
    return any(k in liste for k in kap.split(","))


def _h264_8bit(b: Bilgi) -> bool:
    return b.video == "h264" and b.piksel in ("yuv420p", "yuvj420p", "")


def planla(b: Bilgi, hedef: str, zorla: bool = True) -> Plan:
    """Dosya bilgisine ve hedefe göre ne yapılacağına karar verir.

    DLNA'da cihazın neyi açtığı bilinemediği için `zorla` (kullanıcının "Dönüştür" anahtarı)
    açıksa her zaman MPEG-TS'ye çevrilir. Chromecast'te uyumluysa dokunulmaz.
    """
    sadece_ses = not b.video
    if hedef == CHROMECAST:
        if sadece_ses:
            if b.ses in _CC_SADECE_SES:
                return Plan(DOGRUDAN, hedef, "")
            return Plan(TAM, hedef, "audio/mpeg", sadece_ses=True)
        video_ok = b.video in _CC_VIDEO and (b.video != "h264" or _h264_8bit(b))
        ses_ok = not b.ses or b.ses in _CC_SES
        if video_ok and ses_ok and _kap_icinde(b.kap, _CC_KAP):
            return Plan(DOGRUDAN, hedef, "")
        if _h264_8bit(b):
            return Plan(KAP, hedef, "video/mp4", video_kopya=True, ses_kopya=b.ses in _MP4_SES)
        return Plan(TAM, hedef, "video/mp4")
    # DLNA
    if not zorla:
        return Plan(DOGRUDAN, hedef, "")
    if sadece_ses:
        return Plan(TAM, hedef, "audio/mpeg", sadece_ses=True)
    if _h264_8bit(b):
        return Plan(KAP, hedef, "video/mpeg", video_kopya=True, ses_kopya=b.ses in _TS_SES)
    return Plan(TAM, hedef, "video/mpeg")


def komut(kaynak: str, plan: Plan, baslangic: float = 0.0, ffmpeg: Optional[str] = None) -> List[str]:
    """Çıktıyı stdout'a yazan ffmpeg komutunu kurar."""
    args = [ffmpeg or ffmpeg_yolu() or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if baslangic > 0:
        args += ["-ss", f"{baslangic:.3f}"]  # -i'den önce: hızlı atlama
    args += ["-i", kaynak]
    if plan.sadece_ses:
        return args + ["-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "libmp3lame", "-b:a", "320k",
                       "-ac", "2", "-f", "mp3", "pipe:1"]
    # V: albüm kapağı gibi resimleri saymadan ilk gerçek görüntü
    args += ["-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn"]
    if plan.video_kopya:
        args += ["-c:v", "copy"]
    else:
        args += ["-vf", _OLCEK, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                 "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
                 "-g", "50", "-maxrate", "20M", "-bufsize", "40M"]
    if plan.ses_kopya:
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
    if plan.hedef == CHROMECAST:
        args += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4"]
    else:
        args += ["-f", "mpegts"]
    return args + ["pipe:1"]


# DLNA başlıkları: OP=00 → bayt/zaman ile sarma yok, CI=1 → dönüştürülmüş içerik,
# FLAGS → akış (streaming) aktarımı.
DLNA_OZELLIK = "DLNA.ORG_OP=00;DLNA.ORG_CI=1;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def didl(url: str, mime: str, baslik: str) -> str:
    """SetAVTransportURI için kısa DIDL-Lite (bazı TV'ler türü buradan öğrenmeden açmıyor)."""
    sinif = "object.item.audioItem.musicTrack" if mime.startswith("audio/") else "object.item.videoItem"
    proto = f"http-get:*:{mime}:{DLNA_OZELLIK}"
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(baslik or "Video")}</dc:title>'
            f'<upnp:class>{sinif}</upnp:class>'
            f'<res protocolInfo={quoteattr(proto)}>{escape(url)}</res></item></DIDL-Lite>')


def yerel_mi(kaynak: str) -> bool:
    return os.path.isfile(kaynak)
