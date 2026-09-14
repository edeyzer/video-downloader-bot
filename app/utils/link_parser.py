"""
Automatic platform detection from URL.
"""

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class ParsedLink:
    url: str
    platform: str
    is_valid: bool = True
    media_type: Optional[str] = None  # video, audio, carousel, shorts, reels


# Ordered by specificity (more specific patterns first)
PLATFORM_PATTERNS = [
    # YouTube
    (
        "youtube",
        re.compile(
            r"(?:https?://)?(?:www\.)?"
            r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/|playlist\?list=)|youtu\.be/)"
            r"[\w\-]+",
            re.IGNORECASE,
        ),
    ),
    # Instagram
    (
        "instagram",
        re.compile(
            r"(?:https?://)?(?:www\.)?"
            r"instagram\.com/(?:p|reel|reels|tv|stories)/[\w\-]+",
            re.IGNORECASE,
        ),
    ),
    # TikTok
    (
        "tiktok",
        re.compile(
            r"(?:https?://)?(?:www\.|vm\.|vt\.)?"
            r"tiktok\.com/(?:@[\w\.-]+/video/|t/)?[\w\-]+",
            re.IGNORECASE,
        ),
    ),
    # Pinterest
    (
        "pinterest",
        re.compile(
            r"(?:https?://)?(?:www\.)?"
            r"(?:pinterest\.(?:com|ru|co\.uk)|pin\.it)/[\w\-/]+",
            re.IGNORECASE,
        ),
    ),
    # Twitter / X
    (
        "twitter",
        re.compile(
            r"(?:https?://)?(?:www\.)?"
            r"(?:twitter\.com|x\.com)/[\w]+/status/\d+",
            re.IGNORECASE,
        ),
    ),
    # Facebook
    (
        "facebook",
        re.compile(
            r"(?:https?://)?(?:www\.|m\.|fb\.)?"
            r"(?:facebook\.com|fb\.watch)/[\w\./\?=]+",
            re.IGNORECASE,
        ),
    ),
    # Vimeo
    (
        "vimeo",
        re.compile(
            r"(?:https?://)?(?:www\.)?vimeo\.com/\d+",
            re.IGNORECASE,
        ),
    ),
    # Reddit
    (
        "reddit",
        re.compile(
            r"(?:https?://)?(?:www\.|old\.)?reddit\.com/r/[\w]+/comments/[\w]+",
            re.IGNORECASE,
        ),
    ),
    # SoundCloud
    (
        "soundcloud",
        re.compile(
            r"(?:https?://)?(?:www\.)?soundcloud\.com/[\w\-/]+",
            re.IGNORECASE,
        ),
    ),
    # Twitch clips
    (
        "twitch",
        re.compile(
            r"(?:https?://)?(?:www\.)?(?:twitch\.tv/\w+/clip/|clips\.twitch\.tv/)[\w\-]+",
            re.IGNORECASE,
        ),
    ),
]


def detect_platform(url: str) -> Optional[str]:
    """Return platform name or None if unknown."""
    url = url.strip()
    for platform, pattern in PLATFORM_PATTERNS:
        if pattern.search(url):
            return platform
    return None


def extract_url_from_text(text: str) -> Optional[str]:
    """Extract first valid URL from message text."""
    url_pattern = re.compile(
        r"https?://[^\s<>\"']+|www\.[^\s<>\"']+",
        re.IGNORECASE,
    )
    match = url_pattern.search(text)
    if not match:
        return None
    url = match.group(0)
    if url.startswith("www."):
        url = "https://" + url
    return url.rstrip(".,;:!?)")


def parse_link(text: str) -> Optional[ParsedLink]:
    """
    Main entrypoint.
    Returns ParsedLink or None if no valid supported link found.
    """
    url = extract_url_from_text(text)
    if not url:
        return None

    platform = detect_platform(url)
    if not platform:
        return None

    media_type = None
    lower = url.lower()

    if platform == "youtube":
        if "/shorts/" in lower:
            media_type = "shorts"
        else:
            media_type = "video"
    elif platform == "instagram":
        if "/reel" in lower:
            media_type = "reels"
        elif "/p/" in lower:
            media_type = "post"
        else:
            media_type = "video"
    elif platform == "tiktok":
        media_type = "video"

    return ParsedLink(
        url=url,
        platform=platform,
        is_valid=True,
        media_type=media_type,
    )


def is_supported_url(text: str) -> bool:
    return parse_link(text) is not None