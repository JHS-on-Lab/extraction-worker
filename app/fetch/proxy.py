"""프록시 공급자 인터페이스 + 직접 연결 구현 (프록시 없음)."""

from __future__ import annotations

from typing import Protocol


class ProxyProvider(Protocol):
    def get_proxy(self) -> str | None:
        """현재 사용할 프록시 URL. None이면 직접 연결."""
        ...

    def report_blocked(self, proxy: str | None) -> None:
        """차단된 프록시 보고. 공급자가 교체 로직을 처리한다."""
        ...


class DirectProxy:
    """프록시 없이 직접 연결 (기본 구현)."""

    def get_proxy(self) -> str | None:
        return None

    def report_blocked(self, proxy: str | None) -> None:
        pass
