"""
Cobalt API fallback downloader.
Used when yt-dlp fails. Compatible with Cobalt v10+ API specifications.
"""

import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from app.config import get_settings
from app.core.downloader.base import BaseDownloader, DownloadResult, ProgressCallback

logger = logging.getLogger(__name__)

# DIQQAT: Bu yerda avval qattiq yozilgan (hardcoded) 9 ta ochiq instance
# bor edi. Amalda ularning barchasi endi Turnstile orqali olinadigan JWT
# sessiya tokenini talab qiladi va oddiy server-server so'rovi bilan
# ISHLAMAYDI — har bir muvaffaqiyatsiz yuklashda ularning har biriga
# navbat bilan murojaat qilinib, 8-10 soniya vaqt behuda sarflanardi va
# log'larda "error.api.auth.jwt.missing" spam hosil qilardi (facebook,
# instagram — istalgan platforma uchun bir xil, chunki bu Cobalt'ning
# umumiy API siyosati o'zgarishi, platformaga bog'liq emas).
#
# Shuning uchun ro'yxat endi bo'sh — real ishlaydigan (yoki API-key
# bilan) instance topsangiz, config.py orqali COBALT_PUBLIC_INSTANCES
# muhit o'zgaruvchisida vergul bilan ajratib bering.
DEFAULT_COBALT_INSTANCES: list[str] = []


class CobaltEngine(BaseDownloader):
    def __init__(self, instances: Optional[list[str]] = None):
        settings = get_settings()
        self.local_instance = settings.cobalt_local_url
        self.api_key = settings.cobalt_api_key

        if instances is not None:
            self.instances = instances
        else:
            # Avval o'zimizning (docker-compose'dagi) Cobalt serverimiz
            # sinaladi — u doim ochiq va JWT talab qilmaydi. Faqat u
            # ishlamasa (masalan hali ko'tarilmagan bo'lsa), ochiq
            # (public) ro'yxatga zaxira sifatida o'tiladi.
            self.instances = (
                [self.local_instance] if self.local_instance else []
            ) + (settings.cobalt_public_instances or DEFAULT_COBALT_INSTANCES)

        self.download_dir = Path(settings.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def _request_cobalt(
        self,
        url: str,
        audio_only: bool = False,
        quality: str = "720",
    ) -> Optional[dict]:
        # Cobalt v10+ va standart API parametrlari
        payload = {
            "url": url,
            "downloadMode": "audio" if audio_only else "auto",
            "videoQuality": quality if quality in {"360", "480", "720", "1080", "1440", "2160"} else "720",
            "audioFormat": "mp3",
            "filenameStyle": "basic",
            "youtubeVideoCodec": "h264",
        }
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        }

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for base in self.instances:
                endpoint = f"{base.rstrip('/')}/"
                req_headers = dict(headers)
                if base == self.local_instance and self.api_key:
                    req_headers["Authorization"] = f"Api-Key {self.api_key}"
                try:
                    resp = await client.post(
                        endpoint,
                        json=payload,
                        headers=req_headers,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        status = data.get("status")
                        
                        if status in {"tunnel", "redirect", "stream"} and data.get("url"):
                            return data
                        if status == "picker" and data.get("picker"):
                            item = data["picker"][0]
                            if item.get("url"):
                                return {"url": item["url"], "filename": item.get("filename")}
                                
                    logger.warning(
                        "Cobalt API fallback [%s] returned status: %s body=%s",
                        base, resp.status_code, resp.text[:200],
                    )
                except Exception as e:
                    logger.warning("Cobalt API endpoint [%s] failed: %s", base, e)
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
            progress_callback(5.0, "cobalt: requesting")

        cobalt_data = await self._request_cobalt(url, audio_only=audio_only, quality=quality)
        if not cobalt_data or not cobalt_data.get("url"):
            return DownloadResult(success=False, error="Cobalt API: Unable to process URL across all instances")

        download_url = cobalt_data["url"]
        filename = cobalt_data.get("filename") or f"cobalt_{uuid4().hex[:10]}"
        
        # Sanitize filename va kengaytmani to'g'rilash
        filename = Path(filename).name
        if audio_only and not filename.endswith(".mp3"):
            filename = f"{Path(filename).stem}.mp3"
        elif not audio_only and not Path(filename).suffix:
            filename = f"{filename}.mp4"

        dest = output_dir / filename

        if progress_callback:
            progress_callback(15.0, "cobalt: downloading")

        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0

                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=128 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total and progress_callback:
                                pct = min(15 + (downloaded / total) * 80, 95.0)
                                progress_callback(pct, "cobalt: downloading")

            if progress_callback:
                progress_callback(100.0, "cobalt: done")

            filesize = dest.stat().st_size if dest.exists() else None
            return DownloadResult(
                success=True,
                file_path=dest,
                title=Path(filename).stem,
                filesize=filesize,
                media_type="audio" if audio_only else "video",
                quality=quality,
                platform="cobalt",
            )
        except Exception as e:
            logger.exception("Cobalt download failed during stream")
            if dest.exists():
                dest.unlink(missing_ok=True)
            return DownloadResult(success=False, error=f"Cobalt stream error: {str(e)}")

    async def get_info(self, url: str) -> dict:
        return {"url": url, "extractor": "cobalt"}