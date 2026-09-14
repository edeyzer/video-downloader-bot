"""
Base downloader interface and common result structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    title: Optional[str] = None
    duration: Optional[int] = None          # seconds
    width: Optional[int] = None
    height: Optional[int] = None
    filesize: Optional[int] = None          # bytes
    media_type: str = "video"               # video / audio / photo
    quality: Optional[str] = None
    platform: Optional[str] = None
    error: Optional[str] = None
    raw_info: dict = field(default_factory=dict)


# Progress callback type: (percent: float, status: str) -> None
ProgressCallback = Callable[[float, str], Any]


class BaseDownloader(ABC):
    """Abstract base class for all download engines."""

    @abstractmethod
    async def download(
        self,
        url: str,
        output_dir: Path,
        quality: str = "720",
        audio_only: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        ...

    @abstractmethod
    async def get_info(self, url: str) -> dict:
        ...