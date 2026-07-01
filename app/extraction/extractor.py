"""
추출 진입점.

우선순위:
  1. domain.rules_json 있으면 → RuleEngine (도메인 전용 CSS/XPath 규칙)
  2. 없거나 실패하면        → LibraryChain (trafilatura → readability)

DomainRepo 를 주입받아 규칙을 조회하고, RuleEngine 이 TTL 캐시로 재사용한다.
"""

from __future__ import annotations

from app.extraction.library_chain import LibraryChain
from app.extraction.rule_engine import RuleEngine
from app.types import CollectedContent, ExtractionFailure


class DefaultExtractor:
    def __init__(self, domain_repo=None) -> None:
        self._chain  = LibraryChain()
        self._engine = RuleEngine()
        self._domain_repo = domain_repo  # None 이면 규칙 조회 건너뜀

    def extract(
        self,
        url: str,
        html: str,
        host: str,
        source_type: str = "",
        keyword: str = "",
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        # 1단계: 도메인 전용 규칙 시도
        if self._domain_repo is not None:
            domain_row = self._domain_repo.get(host)
            rules = self._engine.get_rules(host, domain_row)
            if rules:
                result = self._engine.extract(
                    url=url, html=html, host=host,
                    rules=rules, source_type=source_type, keyword=keyword,
                    keyword_id=keyword_id,
                )
                if isinstance(result, CollectedContent):
                    return result
                # json_api 규칙은 LibraryChain 이 도움이 안 되므로 바로 반환
                if "json_api" in rules:
                    return result

        # 2단계: 라이브러리 체인 폴백
        return self._chain.extract(
            url=url, html=html, host=host,
            source_type=source_type, keyword=keyword,
            keyword_id=keyword_id,
        )
