"""
yt-dlp based downloader engine with progress, cookies pool, proxy rotation with blacklist support,
and advanced bot-detection bypass techniques.
"""

import asyncio
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import random
from typing import Any, Optional  # random hali _get_writable_cookie_copy nomlashda ishlatiladi

import yt_dlp

from app.config import get_settings
from app.core.downloader.base import BaseDownloader, DownloadResult, ProgressCallback
from app.core.proxy.rotator import get_proxy_rotator

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=8)


class YtDlpEngine(BaseDownloader):
    def __init__(
        self,
        cookies_file: Optional[str] = None,
        proxy: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        settings = get_settings()
        self.settings = settings
        self._manual_cookies_file = cookies_file
        self.proxy = proxy
        # Root cause this fixes
        # -------------------------------------------------------------
        # _resolve_cookie_file() used to pick a RANDOM file from cookies/
        # (random.choice) regardless of which site the URL belonged to.
        # So an Instagram link had only a 1-in-N chance of actually
        # getting instagram.txt — the rest of the time it silently sent
        # YouTube's or Pinterest's session cookies to Instagram/Facebook,
        # which the target site just ignores (or flags), so yt-dlp fell
        # through to Cobalt almost every time. That's why the "Pinterest
        # bug" kept reappearing on other platforms: it was never platform
        # specific, the cookie selection itself was random.
        self.platform = platform

        self.download_dir = Path(settings.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_cookie_file(self) -> Optional[str]:
        if self._manual_cookies_file and Path(self._manual_cookies_file).exists():
            return self._manual_cookies_file

        if self.settings.ytdlp_cookies_file and Path(self.settings.ytdlp_cookies_file).exists():
            return self.settings.ytdlp_cookies_file

        cookies_dir = Path("cookies")

        # Platform ma'lum bo'lsa — FAQAT o'sha platformaga tegishli cookie
        # fayl ishlatiladi (masalan instagram → cookies/instagram.txt).
        # Boshqa saytning cookie'sini yuborish foydasiz va base(qo'shimcha
        # shubha) tug'diradi, shuning uchun mos fayl topilmasa cookie'siz
        # davom etamiz — noto'g'ri cookie yuborishdan ko'ra yaxshiroq.
        if self.platform:
            platform_file = cookies_dir / f"{self.platform}.txt"
            if platform_file.exists():
                return str(platform_file)
            logger.debug(
                "No cookie file for platform=%s (looked for %s) — continuing without cookies",
                self.platform, platform_file,
            )
            return None

        # Platform noma'lum bo'lsa (masalan get_info umumiy chaqiruv) —
        # eski xatti-harakat sifatida istalgan cookie fayldan foydalanish
        # o'rniga, umuman cookie ishlatmaymiz (xato platformaga cookie
        # yuborishdan ko'ra xavfsizroq).
        return None

    def _get_writable_cookie_copy(self, original_cookie: Optional[str]) -> Optional[str]:
        if not original_cookie or not Path(original_cookie).exists():
            return None

        try:
            temp_dir = Path(tempfile.gettempdir()) / "yt_cookies"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_cookie = temp_dir / f"cookie_{random.randint(10000, 99999)}.txt"
            shutil.copy2(original_cookie, temp_cookie)
            return str(temp_cookie)
        except Exception as e:
            logger.warning("Failed to create writable cookie copy: %s", e)
            return original_cookie

    def _build_opts(
        self,
        output_dir: Path,
        quality: str = "720",
        audio_only: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        proxy: Optional[str] = None,
        cookie_file: Optional[str] = None,
    ) -> dict[str, Any]:
        outtmpl = str(output_dir / "%(id)s.%(ext)s")

        if audio_only:
            format_str = "bestaudio/best"
            postprocessors = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            # Video + Image qo'llab-quvvatlash
            if quality == "best":
                format_str = "bestvideo+bestaudio/best/bestvideo/best"
            else:
                format_str = (
                    f"bestvideo[height<={quality}]+bestaudio/"
                    f"best[height<={quality}]/"
                    f"bestvideo/best"
                )
            postprocessors = [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            ]

        opts: dict[str, Any] = {
            "format": format_str,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "merge_output_format": "mp4",
            "postprocessors": postprocessors,
            "socket_timeout": 30,
            "retries": 5,
            "fragment_retries": 5,
            "ignoreerrors": False,
            "no_color": True,
            "geo_bypass": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "web"],
                    "skip": ["dash", "hls"],
                }
            },
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        }

        if cookie_file and Path(cookie_file).exists():
            opts["cookiefile"] = cookie_file
            logger.debug("Using cookies: %s", cookie_file)

        if proxy:
            opts["proxy"] = proxy
            logger.debug("Using proxy: %s", proxy.split("@")[-1] if "@" in proxy else proxy)

        if progress_callback:
            def _hook(d: dict):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    if total > 0:
                        percent = min(downloaded / total * 100, 99.0)
                        progress_callback(percent, "downloading")
                elif d.get("status") == "finished":
                    progress_callback(100.0, "processing")

            opts["progress_hooks"] = [_hook]

        return opts

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

        rotator = get_proxy_rotator()
        current_proxy = self.proxy or rotator.get()
        original_cookie = self._resolve_cookie_file()
        current_cookie = self._get_writable_cookie_copy(original_cookie)

        opts = self._build_opts(
            output_dir=output_dir,
            quality=quality,
            audio_only=audio_only,
            progress_callback=progress_callback,
            proxy=current_proxy,
            cookie_file=current_cookie,
        )

        def _run() -> DownloadResult:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        return DownloadResult(success=False, error="No info returned from URL")

                    vid = info.get("id", "")
                    
                    filepath: Optional[Path] = None
                    if audio_only:
                        target_file = output_dir / f"{vid}.mp3"
                        if target_file.exists():
                            filepath = target_file
                    else:
                        target_file = output_dir / f"{vid}.mp4"
                        if target_file.exists():
                            filepath = target_file

                    if not filepath or not filepath.exists():
                        candidates = list(output_dir.glob(f"{vid}.*"))
                        if candidates:
                            filepath = candidates[0]

                    if not filepath or not filepath.exists():
                        return DownloadResult(
                            success=False,
                            error="Downloaded file could not be found on disk",
                        )

                    filesize = filepath.stat().st_size

                    # Media turini aniqlash
                    ext = filepath.suffix.lower()
                    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
                        detected_media_type = "image"
                    elif audio_only or ext in {".mp3", ".m4a", ".ogg", ".opus", ".flac"}:
                        detected_media_type = "audio"
                    else:
                        detected_media_type = "video"

                    return DownloadResult(
                        success=True,
                        file_path=filepath,
                        title=info.get("title"),
                        duration=info.get("duration"),
                        width=info.get("width"),
                        height=info.get("height"),
                        filesize=filesize,
                        media_type=detected_media_type,
                        quality=quality,
                        platform=info.get("extractor_key", "").lower(),
                        raw_info=info,
                    )
            except yt_dlp.utils.DownloadError as e:
                logger.error("yt-dlp DownloadError: %s (Proxy used: %s)", e, current_proxy)
                if current_proxy:
                    rotator.mark_bad(current_proxy)
                return DownloadResult(success=False, error=f"Download failed: {str(e)}")
            except Exception as e:
                logger.exception("Unexpected download error")
                if current_proxy:
                    rotator.mark_bad(current_proxy)
                return DownloadResult(success=False, error=f"Unexpected error: {str(e)}")
            finally:
                if current_cookie and current_cookie != original_cookie:
                    try:
                        Path(current_cookie).unlink(missing_ok=True)
                    except Exception:
                        pass

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(_EXECUTOR, _run)
            return result
        except Exception as e:
            logger.exception("Executor execution failed")
            return DownloadResult(success=False, error=str(e))

    async def get_info(self, url: str) -> dict:
        rotator = get_proxy_rotator()
        proxy = self.proxy or rotator.get()
        original_cookie = self._resolve_cookie_file()
        cookie_file = self._get_writable_cookie_copy(original_cookie)

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "web"],
                }
            },
        }
        if cookie_file and Path(cookie_file).exists():
            opts["cookiefile"] = cookie_file

        if proxy:
            opts["proxy"] = proxy

        def _run() -> dict:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False) or {}
            except Exception as e:
                logger.error("Failed to fetch info: %s", e)
                if proxy:
                    rotator.mark_bad(proxy)
                return {}
            finally:
                if cookie_file and cookie_file != original_cookie:
                    try:
                        Path(cookie_file).unlink(missing_ok=True)
                    except Exception:
                        pass

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_EXECUTOR, _run)