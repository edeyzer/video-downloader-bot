"""
Pinterest image fallback engine.

Root cause this fixes
----------------------
yt-dlp's Pinterest extractor only understands VIDEO pins. If a pin is an
image (no video stream at all), yt-dlp raises:
    ERROR: [Pinterest] <id>: No video formats found!
This is expected behaviour on yt-dlp's side, not a bug — an image pin
literally has no "format" to select. The worker then fell through to the
Cobalt fallback, but Cobalt's public instances don't extract Pinterest
*images* either (Cobalt is video/audio focused), so every instance
correctly rejects the request with 400 and the user sees a generic
"download failed" message even though the content is perfectly
downloadable — it's just a picture, not a video.

This engine resolves the pin (following pin.it short links) and pulls the
original-resolution image directly from the page's Open Graph metadata,
so image pins are delivered as a photo instead of failing outright.
"""

import logging
import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from app.config import get_settings
from app.core.downloader.base import BaseDownloader, DownloadResult, ProgressCallback

logger = logging.getLogger(__name__)

_HEADERS = {
    # Oddiy brauzer (Chrome) User-Agent bilan Pinterest ko'pincha bo'sh SPA
    # qobig'ini qaytaradi — asl rasm u yerda JavaScript orqali yuklanadi,
    # va biz "standart/placeholder" og:image'ni olib qolamiz. Telegram esa
    # shu havolani TO'G'RI ko'rsatadi, chunki u o'zining bot User-Agent'i
    # bilan so'raydi — shunga javoban Pinterest (boshqa saytlar kabi)
    # tayyor, to'g'ri OG-teglar bilan pre-rendered HTML qaytaradi. Xuddi
    # shu Telegram bot User-Agent'ini ishlatib, xuddi shu natijani olamiz.
    "User-Agent": "TelegramBot (like TwitterBot)",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

# Pinterest CDN thumbnail path looks like /564x/ or /236x/ — swap for /originals/
_CDN_SIZE_RE = re.compile(r"/\d+x\d*/")
# Pin ID appears in canonical, share and even "/sent/" tracking URLs alike
_PIN_ID_RE = re.compile(r"/pin/(\d+)")

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_CONTENT_ATTR_RE = re.compile(r'content=["\']([^"\']+)["\']', re.IGNORECASE)

# Pinterestning o'zi Open Graph teglaridan tashqari, sahifa ichidagi
# hydration/JSON ma'lumotlarida ham to'liq pinimg.com rasm URL'larini olib
# yuradi. Bu tegning atributlar tartibiga yoki SPA render holatiga umuman
# bog'liq emas — shuning uchun ENG ISHONCHLI usul: shunchaki xom HTML
# ichidan pinimg.com rasm havolasini qidirish.
_PINIMG_URL_RE = re.compile(
    r'https://i\.pinimg\.com/(?:originals|\d{2,4}x\d*)/[a-zA-Z0-9/_\-]+\.(?:jpg|jpeg|png|webp|gif)',
    re.IGNORECASE,
)


def _extract_meta_content(html: str, *prop_names: str) -> Optional[str]:
    """og:image ni <meta> tegidan ATRIBUTLAR TARTIBIGA QARAMASDAN topadi.

    Ba'zi saytlar (jumladan Pinterest) content="..." ni property="..."dan
    OLDIN yozadi — oddiy regex faqat property→content tartibini kutgani
    uchun mos kelmasdi. Bu funksiya butun <meta ...> tegini olib, ichidan
    property/name va content'ni alohida-alohida qidiradi, tartibi muhim
    emas.
    """
    lower_names = {n.lower() for n in prop_names}
    for tag in _META_TAG_RE.findall(html):
        tag_lower = tag.lower()
        if any(f'"{n}"' in tag_lower or f"'{n}'" in tag_lower for n in lower_names):
            m = _CONTENT_ATTR_RE.search(tag)
            if m:
                return m.group(1)
    return None


def _find_pin_image_url(html: str) -> Optional[str]:
    """Pin rasm URL'ini topadi.

    MUHIM: avval <meta property="og:image"> ga tayanamiz, chunki u aynan
    SHU pin uchun mo'ljallangan yagona, kafolatlangan rasm. Xom HTML ichidan
    pinimg.com havolasini shunchaki qidirish xato natija berishi mumkin —
    Pinterest sahifasida asl pindan tashqari "shunga o'xshash pinlar" tavsiya
    bloki ham bo'ladi, va ularning ham pinimg.com rasm havolalari bor;
    ularning birortasi tasodifan HTML'da og:image tegidan OLDIN kelib
    qolsa, noto'g'ri (butunlay boshqa) rasm tanlab olinishi mumkin edi.
    Shu sabab endi bu funksiya faqat meta-teg topilmagan taqdirdagina xom
    qidiruvga tayanadi.
    """
    og_image = _extract_meta_content(html, "og:image", "og:image:secure_url")
    if og_image:
        return _CDN_SIZE_RE.sub("/originals/", og_image.replace("&amp;", "&"))

    twitter_image = _extract_meta_content(html, "twitter:image")
    if twitter_image:
        return _CDN_SIZE_RE.sub("/originals/", twitter_image.replace("&amp;", "&"))

    # Oxirgi chora: xom HTML'dagi birinchi pinimg.com havolasi
    # (meta teg topilmagan hollar uchun, noto'g'ri rasm xavfi bor).
    urls = _PINIMG_URL_RE.findall(html)
    if urls:
        for u in urls:
            if "/originals/" in u:
                return u
        return _CDN_SIZE_RE.sub("/originals/", urls[0])
    return None


class PinterestImageEngine(BaseDownloader):
    """Last-resort engine for Pinterest image pins (no video stream exists)."""

    def __init__(self):
        settings = get_settings()
        self.download_dir = Path(settings.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def download(
        self,
        url: str,
        output_dir: Optional[Path] = None,
        quality: str = "720",
        audio_only: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        output_dir = output_dir or self.download_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback(10.0, "pinterest: rasm qidirilmoqda")

        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, headers=_HEADERS
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
                final_url = str(resp.url)

                # pin.it shortlinks (va ba'zan share/invite linklar) ko'pincha
                # asl pin sahifasiga emas, balki Pinterest'ning "/sent/" kuzatuv
                # (taklif) sahifasiga olib boradi — u yerda og:image bo'lmaydi.
                # Shu sababli, redirect zanjirida pin ID topilsa, kanonik
                # pin sahifasini ALOHIDA so'rab, undan foydalanamiz.
                pin_id_match = _PIN_ID_RE.search(final_url) or _PIN_ID_RE.search(url)
                if pin_id_match:
                    canonical_url = f"https://www.pinterest.com/pin/{pin_id_match.group(1)}/"
                    try:
                        canon_resp = await client.get(canonical_url)
                        if canon_resp.status_code == 200 and (
                            "pinimg.com" in canon_resp.text or "og:image" in canon_resp.text
                        ):
                            html = canon_resp.text
                    except Exception:
                        pass  # asl (dastlabki) html bilan davom etamiz

                image_url = _find_pin_image_url(html)
                if not image_url:
                    logger.warning(
                        "Pinterest: rasm URL topilmadi (html_len=%d, has_og_image_str=%s, has_pinimg_str=%s) url=%s",
                        len(html), "og:image" in html, "pinimg.com" in html, url[:80],
                    )
                    return DownloadResult(
                        success=False,
                        error="Pinterest: rasm topilmadi (post yopiq yoki o'chirilgan bo'lishi mumkin)",
                    )

                title = _extract_meta_content(html, "og:title")
                if title:
                    title = title.replace("&amp;", "&")

                logger.info("Pinterest image fallback: pin_id=%s image_url=%s", pin_id_match.group(1) if pin_id_match else "?", image_url)

                if progress_callback:
                    progress_callback(40.0, "pinterest: rasm yuklanmoqda")

                img_resp = await client.get(image_url)
                if img_resp.status_code != 200 and "/originals/" in image_url:
                    # /originals/ ba'zan 404 beradi (masalan pin private/deleted
                    # bo'lsa asl hajm olib tashlangan bo'ladi) — CDN'ning eng katta
                    # ma'lum o'lchamiga (736x) qaytib ko'ramiz.
                    fallback_url = _CDN_SIZE_RE.sub("/736x/", image_url, count=0) \
                        if _CDN_SIZE_RE.search(image_url) else image_url.replace("/originals/", "/736x/")
                    img_resp = await client.get(fallback_url)
                    if img_resp.status_code == 200:
                        image_url = fallback_url
                img_resp.raise_for_status()

                ext = ".jpg"
                content_type = img_resp.headers.get("content-type", "")
                if "png" in content_type:
                    ext = ".png"
                elif "webp" in content_type:
                    ext = ".webp"
                elif "gif" in content_type:
                    ext = ".gif"

                dest = output_dir / f"pinterest_{uuid4().hex[:10]}{ext}"
                dest.write_bytes(img_resp.content)

            if progress_callback:
                progress_callback(100.0, "pinterest: tayyor")

            return DownloadResult(
                success=True,
                file_path=dest,
                title=title,
                filesize=dest.stat().st_size,
                media_type="image",
                platform="pinterest",
            )
        except Exception as e:
            logger.warning("Pinterest image fallback failed for %s: %s", url[:80], e)
            return DownloadResult(success=False, error=f"Pinterest rasm fallback xatosi: {e}")

    async def get_info(self, url: str) -> dict:
        return {"url": url, "extractor": "pinterest_image"}
