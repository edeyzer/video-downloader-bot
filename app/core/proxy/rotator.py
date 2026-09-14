"""
Dynamic Proxy Rotator & Blacklist Manager.
Supports Round-Robin, Random selection, and temporary Blacklisting of failing proxies.
"""

import itertools
import logging
import random
import time
from typing import List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


class ProxyRotator:
    def __init__(self, proxies: Optional[List[str]] = None):
        settings = get_settings()
        self._raw_proxies: List[str] = proxies if proxies is not None else (settings.proxies or [])
        self._blacklisted: dict[str, float] = {}  # {proxy_url: ban_timestamp}
        self._blacklist_ttl = 300  # Ishlamagan proxy'ni 5 daqiqa bloklash
        self._cycle = itertools.cycle(self._raw_proxies) if self._raw_proxies else None

    def _clean_blacklist(self):
        """Muddati o'tgan bloklangan proxy'larni ro'yxatdan chiqarish."""
        now = time.time()
        expired = [p for p, ts in self._blacklisted.items() if now - ts > self._blacklist_ttl]
        for p in expired:
            self._blacklisted.pop(p, None)

    @property
    def valid_proxies(self) -> List[str]:
        """Faqatgina bloklanmagan (sog'lom) proxy'lar ro'yxati."""
        self._clean_blacklist()
        return [p for p in self._raw_proxies if p not in self._blacklisted]

    def get(self) -> Optional[str]:
        """
        Round-robin: Navbatdagi sog'lom proxy'ni qaytaradi.
        """
        valids = self.valid_proxies
        if not valids:
            if self._raw_proxies:
                logger.warning("Barcha proxy'lar blacklist'da! Standard IP'ga fallback qilinadi.")
            return None

        # Cycle generator orqali navbatdagisini olish (agar blacklist'da bo'lsa keyingisiga o'tadi)
        if not self._cycle:
            self._cycle = itertools.cycle(self._raw_proxies)

        for _ in range(len(self._raw_proxies)):
            proxy = next(self._cycle)
            if proxy in valids:
                return proxy

        return None

    def get_random(self) -> Optional[str]:
        """
        Tasodifiy sog'lom proxy.
        """
        valids = self.valid_proxies
        if not valids:
            return None
        return random.choice(valids)

    def mark_bad(self, proxy_url: str):
        """Xatolik bergan proxy'ni 5 daqiqaga bloklash."""
        if proxy_url:
            logger.warning("Proxy xatolik berdi va 5 minutga blacklist'ga tushdi: %s", proxy_url)
            self._blacklisted[proxy_url] = time.time()

    @property
    def has_proxies(self) -> bool:
        return len(self.valid_proxies) > 0

    @property
    def count(self) -> int:
        return len(self.valid_proxies)


_rotator: Optional[ProxyRotator] = None


def get_proxy_rotator() -> ProxyRotator:
    global _rotator
    if _rotator is None:
        _rotator = ProxyRotator()
    return _rotator


# Mukammal integratsiya uchun tayyor obyekt
proxy_rotator = get_proxy_rotator()