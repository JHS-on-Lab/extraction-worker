"""
포트(Port) 인터페이스 — 설계 문서 4.1절.

모든 구현체는 여기 정의된 Protocol을 만족해야 한다.
구현체끼리는 서로를 직접 임포트하지 않고 이 포트를 통해서만 소통한다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.types import CollectedContent, DiscoverResult, ExtractionFailure, FetchResult, RenderMode


@runtime_checkable
class SourceAdapter(Protocol):
    """
    소스별 발견 어댑터.
    검색 결과 페이지를 스크래핑해 콘텐츠 URL 목록과 다음 cursor를 반환한다.
    본문은 건드리지 않는다.
    """
    source_type: str

    def discover(self, keyword: str, cursor: str | None) -> DiscoverResult:
        """
        keyword를 검색해 콘텐츠 URL 목록을 반환.
        cursor: 이전 호출의 next_cursor (첫 호출은 None).
        """
        ...


@runtime_checkable
class Fetcher(Protocol):
    """
    네트워크 요청 추상화.
    프록시·레이트리밋·재시도는 구현체 내부에서 처리한다.
    호출부는 URL만 넘기면 된다.
    """

    def fetch(self, url: str, *, render: RenderMode = RenderMode.STATIC) -> FetchResult:
        """
        static(HTTP) 우선, render=HEADLESS면 브라우저로 렌더링.
        네트워크 수준 오류는 FetchResult 대신 예외를 올린다.
        """
        ...


@runtime_checkable
class Extractor(Protocol):
    """
    HTML → CollectedContent 추출.
    규칙이 있으면 규칙 우선, 없으면 라이브러리 체인.
    성공/실패 판정(본문 길이·제목 비어있음)까지 포함한다.
    """

    def extract(
        self,
        url: str,
        html: str,
        host: str,
        source_type: str = "",
        keyword: str = "",
        keyword_id: int | None = None,
    ) -> CollectedContent | ExtractionFailure:
        """
        성공 시 CollectedContent, 실패 시 ExtractionFailure 반환.
        예외를 올리지 않고 ExtractionFailure로 감싸서 반환한다.
        """
        ...


@runtime_checkable
class Sink(Protocol):
    """
    수집된 콘텐츠 저장소.
    구현체: FileSink(.jsonl), SolrSink.
    호출부 코드는 변경 없이 Sink 인터페이스만 사용한다.

    write() 는 실제로 영속화됐다는 보장이 없다(SolrSink 는 버퍼링만 함) — 호출부는
    batch_size 개마다(또는 idle/heartbeat/종료 시점에) flush() 를 명시적으로 호출해
    성공을 확인한 뒤에야 그 항목들을 "저장 완료"로 취급해야 한다.
    """

    batch_size: int  # 이 개수만큼 write() 가 쌓이면 flush() 를 호출하라는 신호. FileSink=1(즉시), SolrSink=SOLR_BATCH_SIZE

    def write(self, content: CollectedContent) -> None:
        """콘텐츠를 버퍼에 쓴다(또는 즉시 기록). SolrSink는 crawl_id(url)로 멱등 upsert, FileSink는 append."""
        ...

    def flush(self) -> None:
        """버퍼링된 내용을 실제로 저장소에 반영한다. 실패 시 예외를 올린다(호출부가 대응)."""
        ...
