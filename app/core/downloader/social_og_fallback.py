"""
Generic Instagram / Facebook fallback engine (Open Graph based).

Root cause this fixes
----------------------
Instagram and Facebook posts are not always videos - a lot of them are
plain photos (or photo carousels). yt-dlp's extractors for both sites
only understand VIDEO content; for a photo-only post they fail with
something like:
    [Instagram] <id>: There is no video in this post
    [facebook] <id>: This video is only available for registered users...
Before this fix, that failure fell through to the Cobalt fallback, and
Cobalt/the main post page returned CONTENT UNRELATED to the actual post
(a random ad video, or - for Instagram - a completely different photo).
That happens because both sites serve a generic/anti-scraping page to
plain HTTP fetches instead of the real post content.

Two techniques are used here instead of a single blind OG scrape:
1. For Instagram, the dedicated `/embed/captioned/` page is used first.
   This is Instagram's own public oEmbed-style preview page - it is
   MUCH less aggressively anti-scraped than the main post page and
   reliably contains the single real image/video for that exact post
   (no "suggested posts" noise that can pollute a generic meta-tag
   scrape of the main page).
2. For everything else (Facebook), the main page's own og:video/
   og:image tags are read directly, optionally authenticated with the
   platform's cookie file for content that requires login.

`probe_kind()` lets the bot find out UP FRONT (before ever showing a
"Video / Audio" choice) whether a link is a photo or a video, so photo
links can be delivered straight away with no irrelevant prompt.
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

# Telegram/Twitter/Facebook link-preview bots get a pre-rendered page with
# real Open Graph tags instead of the JS-only SPA shell a normal browser
# UA would receive.
_HEADERS = {
    "User-Agent": "TelegramBot (like TwitterBot)",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_CONTENT_ATTR_RE = re.compile(r'content=["\']([^"\']+)["\']', re.IGNORECASE)

_IG_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# Instagram'ning captioned-embed sahifasida VIDEO post uchun <video> tegi
# chiqadi, lekin manba URL deyarli har doim <video>'ning O'ZIDA emas,
# uning ICHIDAGI <source src="..."> teg(lar)i(da) bo'ladi:
#   <video class="EmbeddedMediaVideo" poster="...jpg" ...>
#       <source src="....mp4" type="video/mp4">
#   </video>
# Eski regex faqat <video ... src="...">'ni qidirar edi va bu holatda
# HECH QACHON moslashmasdi -> video post "aniqlanmadi" bo'lib qolib,
# pastdagi asosiy-sahifa (og:tag) fallback'iga tushardi. Instagram esa
# login qilinmagan asosiy sahifada deyarli doim faqat og:image (poster
# rasm)ni ochiq qo'yadi, og:video'ni esa bloklaydi — natijada VIDEO
# post ham "RASM" deb noto'g'ri aniqlanardi.
_IG_EMBED_VIDEO_TAG_RE = re.compile(r"<video\b", re.IGNORECASE)
_IG_EMBED_VIDEO_RE = re.compile(r'<video[^>]+src="([^"]+)"', re.IGNORECASE)
_IG_EMBED_SOURCE_SRC_RE = re.compile(r'<source[^>]+src="([^"]+)"', re.IGNORECASE)
_IG_EMBED_IMAGE_RE = re.compile(
    r'<img[^>]+class="[^"]*EmbeddedMediaImage[^"]*"[^>]+src="([^"]+)"', re.IGNORECASE
)


def _extract_meta_content(html: str, *prop_names: str) -> Optional[str]:
    lower_names = {n.lower() for n in prop_names}
    for tag in _META_TAG_RE.findall(html):
        tag_lower = tag.lower()
        if any(f'"{n}"' in tag_lower or f"'{n}'" in tag_lower for n in lower_names):
            m = _CONTENT_ATTR_RE.search(tag)
            if m:
                return m.group(1).replace("&amp;", "&")
    return None


def _instagram_embed_url(url: str) -> Optional[str]:
    m = _IG_SHORTCODE_RE.search(url)
    if not m:
        return None
    return f"https://www.instagram.com/p/{m.group(1)}/embed/captioned/"


class SocialOgFallbackEngine(BaseDownloader):
    """Fallback engine for Instagram/Facebook posts that yt-dlp can't pull
    a video out of - usually because the post is a photo, not a video.
    """

    def __init__(self, platform: str, cookies_file: Optional[str] = None):
        settings = get_settings()
        self.platform = platform
        self.download_dir = Path(settings.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        if cookies_file and Path(cookies_file).exists():
            self.cookies_file = cookies_file
        else:
            candidate = Path("cookies") / f"{platform}.txt"
            self.cookies_file = str(candidate) if candidate.exists() else None

    def _load_cookie_header(self) -> dict:
        """Netscape cookie faylini oddiy 'Cookie:' headeriga aylantiradi."""
        if not self.cookies_file:
            return {}
        try:
            pairs = []
            for line in Path(self.cookies_file).read_text(errors="ignore").splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    name, value = parts[5], parts[6]
                    pairs.append(f"{name}={value}")
            if pairs:
                return {"Cookie": "; ".join(pairs)}
        except Exception as e:
            logger.debug("Could not read cookie file %s: %s", self.cookies_file, e)
        return {}

    async def _resolve_media(self, client: httpx.AsyncClient, url: str, audio_only: bool):
        """(media_type, media_url, title) ni topadi - hech narsa yuklamaydi."""

        # Embed bosqichida "bu post - VIDEO" ekani aniqlansa-yu, biroq
        # ijro etsa bo'ladigan manba URL topilmasa, shu belgi ko'tariladi -
        # bu holatda pastdagi asosiy-sahifa fallback'i uni RASM deb
        # noto'g'ri belgilamasligi kerak (chunki Instagram login qilinmagan
        # asosiy sahifada video postlar uchun ham deyarli doim faqat
        # og:image/thumbnail ochiq qoladi, og:video esa bloklanadi).
        confirmed_video_no_source = False

        if self.platform == "instagram":
            embed_url = _instagram_embed_url(url)
            if embed_url:
                try:
                    resp = await client.get(embed_url)
                    if resp.status_code == 200:
                        html = resp.text
                        title = _extract_meta_content(html, "og:title")

                        if _IG_EMBED_VIDEO_TAG_RE.search(html):
                            if not audio_only:
                                video_m = (
                                    _IG_EMBED_VIDEO_RE.search(html)
                                    or _IG_EMBED_SOURCE_SRC_RE.search(html)
                                )
                                video_url = (
                                    video_m.group(1).replace("&amp;", "&")
                                    if video_m
                                    else _extract_meta_content(
                                        html, "og:video", "og:video:url", "og:video:secure_url"
                                    )
                                )
                                if video_url:
                                    return "video", video_url, title
                            confirmed_video_no_source = True
                        else:
                            img_m = _IG_EMBED_IMAGE_RE.search(html)
                            if img_m:
                                return "image", img_m.group(1).replace("&amp;", "&"), title
                except Exception as e:
                    logger.debug("Instagram embed page fetch failed for %s: %s", url[:80], e)
            # Embed sahifa ishlamasa (masalan xususiy post) - asosiy sahifa
            # OG teglariga zaxira sifatida qaytamiz.

        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

        video_url = _extract_meta_content(html, "og:video", "og:video:url", "og:video:secure_url")
        image_url = _extract_meta_content(html, "og:image", "og:image:secure_url")
        title = _extract_meta_content(html, "og:title", "og:description")

        if video_url and not audio_only:
            return "video", video_url, title
        if image_url and not confirmed_video_no_source:
            return "image", image_url, title
        return None, None, title

    async def probe_kind(self, url: str) -> Optional[str]:
        """Faylni yuklamasdan, u VIDEO yoki RASM ekanini tez aniqlaydi.

        Link yuborilgan zahoti (Video/Audio tugmalarini ko'rsatishdan
        OLDIN) chaqiriladi - rasm bo'lsa, foydalanuvchidan formatni
        so'rashning hojati yo'q.
        """
        headers = dict(_HEADERS)
        headers.update(self._load_cookie_header())
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True, headers=headers
            ) as client:
                media_type, media_url, _ = await self._resolve_media(client, url, audio_only=False)
                return media_type if media_url else None
        except Exception as e:
            logger.debug("%s probe_kind failed for %s: %s", self.platform, url[:80], e)
            return None

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
            progress_callback(10.0, f"{self.platform}: media qidirilmoqda")

        headers = dict(_HEADERS)
        headers.update(self._load_cookie_header())

        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, headers=headers
            ) as client:
                media_type, media_url, title = await self._resolve_media(client, url, audio_only)

                if not media_url:
                    return DownloadResult(
                        success=False,
                        error=(
                            f"{self.platform}: post ochilmadi (yopiq/o'chirilgan "
                            f"bo'lishi yoki login talab qilishi mumkin)"
                        ),
                    )

                if progress_callback:
                    progress_callback(40.0, f"{self.platform}: yuklanmoqda")

                media_resp = await client.get(media_url)
                media_resp.raise_for_status()

                content_type = media_resp.headers.get("content-type", "")
                if media_type == "video":
                    ext = ".mp4"
                else:
                    ext = ".jpg"
                    if "png" in content_type:
                        ext = ".png"
                    elif "webp" in content_type:
                        ext = ".webp"

                dest = output_dir / f"{self.platform}_{uuid4().hex[:10]}{ext}"
                dest.write_bytes(media_resp.content)

            if progress_callback:
                progress_callback(100.0, f"{self.platform}: tayyor")

            return DownloadResult(
                success=True,
                file_path=dest,
                title=title,
                filesize=dest.stat().st_size,
                media_type=media_type,
                platform=self.platform,
            )
        except Exception as e:
            logger.warning("%s OG fallback failed for %s: %s", self.platform, url[:80], e)
            return DownloadResult(
                success=False, error=f"{self.platform} fallback xatosi: {e}"
            )

    async def get_info(self, url: str) -> dict:
        return {"url": url, "extractor": f"{self.platform}_og"}
