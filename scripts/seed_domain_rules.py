"""
domain 테이블 규칙 시드 스크립트.

테이블을 날렸거나 규칙을 초기화해야 할 때 실행한다.
이미 존재하는 host 는 rules_json / render_mode / crawl_delay_ms 를 덮어쓴다.

실행: python scripts/seed_domain_rules.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app import config
from app.repository.db import db_context

# ---------------------------------------------------------------------------
# 도메인 규칙 정의
# 각 항목:
#   host          : 도메인 (PK)
#   rules_json    : 추출 규칙 (None 이면 규칙 없이 render_mode 설정만)
#   rules_enabled : 규칙 활성화 여부
#   render_mode   : static | headless | headless_with_iframe
#   crawl_delay_ms: 요청 간 최소 대기 (ms). None 이면 전역 기본값 사용
#   updated_by    : 등록자 메모
# ---------------------------------------------------------------------------

_RULES: list[dict] = [

    # ==========================================================================
    # JSON API 직접 호출
    # ==========================================================================

    {
        "host": "finance.naver.com",
        "render_mode": "static",
        "crawl_delay_ms": 500,
        "rules_enabled": True,
        "updated_by": "seed",
        # React SPA iframe — CSS 추출 불가, JSON API 직접 호출
        "rules_json": {
            "json_api": {
                "url_template": "https://m.stock.naver.com/front-api/discussion/detail?id={nid}",
                "url_param":    "nid",
                "title":        "result.title",
                "body_html":    "result.contentHtml",
                "body_css":     ".se-module-text",
                "published_at": "result.writtenAt",
                "author":       "result.writer.nickname",
            },
            "min_body_len": 5,
        },
    },

    # ==========================================================================
    # Daum 뷰어 (제휴 언론사 콘텐츠)
    # ==========================================================================

    {
        "host": "v.daum.net",
        "render_mode": "static",
        "crawl_delay_ms": 500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # div.article_view: 본문만 포함. viewrelate_wrap(관련콘텐츠)·저작권 문구 자동 제외.
        "rules_json": {
            "title":    {"css": "h3.tit_view"},
            "body":     {"css": "div.article_view"},
            "min_body_len": 100,
        },
    },

    # ==========================================================================
    # SPA / JavaScript 렌더링 필요 → render_mode: headless
    # trafilatura/readability 가 빈 HTML 만 보기 때문에 PARSE_ERROR 발생
    # rules_json 없이 headless fetch 후 LibraryChain 폴백으로 처리
    # ==========================================================================

    {
        "host": "news.jtbc.co.kr",
        "render_mode": "headless",
        "crawl_delay_ms": 2000,
        "rules_enabled": True,
        "updated_by": "domain-analysis-2",
        # 2026-07 사이트 리뉴얼로 div#ijam_content 컨테이너 자체가 사라짐(신규 확인).
        # 새 본문 컨테이너: [data-testid="article-body"]. 단, 이 컨테이너는
        # domcontentloaded 시점엔 아직 없고 클라이언트 하이드레이션 후에야 생기며,
        # 하이드레이션 직후에도 비디오 플레이어 관련 노드가 먼저 채워짐.
        # headless_wait_for 를 실제 본문 문단(span.MuiTypography-body-md)이
        # 나타나는 시점으로 바꿔 레이스 컨디션(빈 컨테이너만 캡처되는 문제) 방지.
        # author: a.author-item 은 그대로 유효.
        # published_at: span 텍스트가 "입력 YYYY.MM.DD HH:MM" 형태 →
        #   XPath substring-after 로 날짜 부분만 추출 (그대로 유효).
        "rules_json": {
            "headless_wait_for": "span.MuiTypography-body-md",
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"css": "[data-testid='article-body'] span.MuiTypography-body-md"},
            "author":       {"css": "a.author-item"},
            "published_at": {"xpath": "substring-after((//span[starts-with(., '입력 ') and not(contains(., '수정'))])[1], '입력 ')",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 200,
        },
    },
    {
        "host": "www.ichannela.com",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 채널A 뉴스 — 전통적 SSR. 정적 fetch로 본문 수신 가능
        # published_at: <p class="news_view_day">날짜 <span>카테고리</span></p>
        #   → css로 꺼내면 카테고리 텍스트가 붙어 파싱 실패. XPath text()[1]로 순수 날짜만 추출.
        # author: <meta name="author"> 에 기자 이름만 깔끔하게 들어있음.
        "rules_json": {
            "title":        {"css": "div.news_title_area h1"},
            "body":         {"css": "div.news_artice_area"},
            "author":       {"xpath": "//meta[@name='author']/@content"},
            "published_at": {"xpath": "//p[@class='news_view_day']/text()[1]",
                             "date_format": "%Y-%m-%d %H:%M"},
            "min_body_len": 100,
        },
    },
    {
        "host": "biz.sbs.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 순수 CSR이나 /amp/article/{id} 에 정적 본문 존재 → amp_url 변환으로 처리
        # published_at: span.aeti_num 이 입력/수정 두 개 매칭되어 "\n" 포함 문자열이 됨
        #   → div.aeti_date_entry 로 범위를 좁혀 입력 날짜만 추출.
        # author: <strong class="aeti_title"> 에 기자명이 포함됨 ("SBS Biz 안지혜" 형태).
        "rules_json": {
            "amp_url":      {"pattern": "/article/", "replacement": "/amp/article/"},
            "title":        {"css": "h2.titleline_title_end"},
            "body":         {"css": "div.acem_text"},
            "author":       {"css": "strong.aeti_title"},
            "published_at": {"css": "div.aeti_date_entry span.aeti_num",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 뉴스1 ──────────────────────────────────────────────────────────────────
    {
        "host": "www.news1.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # Next.js Pages Router — 정적 HTML 의 __NEXT_DATA__ 에 콘텐츠 데이터 임베드.
        # 본문은 contentArrange 배열에서 type=text 항목의 content 를 이어 붙여 구성.
        "rules_json": {
            "next_data": {
                "root":             "props.pageProps.articleView",
                "title":            "title",
                "author":           "author",
                "published_at":     "published_time",
                "body_array":       "contentArrange",
                "body_type_key":    "type",
                "body_type_value":  "text",
                "body_content_key": "content",
            },
            "min_body_len": 100,
        },
    },

    # ==========================================================================
    # 페이월 → rules_enabled: False (추출 시도 자체를 건너뜀)
    # 영구 실패로 처리해 재시도 소비 방지
    # ==========================================================================

    {
        "host": "www.nytimes.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": False,
        "rules_json": None,
        "updated_by": "domain-analysis",
        # NYT 유료 구독 페이월 — 본문 접근 불가
    },
    {
        "host": "www.thebell.co.kr",
        "render_mode": "headless",
        "crawl_delay_ms": 2000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # CSR — JS 실행 후 div#article_main 에 본문 주입. XPath로 script 태그 제외.
        # 유료 기사는 본문 자리에 페이월 메시지가 짧게 들어와 min_body_len 으로 필터됨.
        "rules_json": {
            "headless_wait_for": "div#article_main p",
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"xpath": "//div[@id='article_main']//text()[not(ancestor::script) and normalize-space()]"},
            "author":       {"css": "div.userBox"},
            "published_at": {"css": "span.date", "date_format": "%Y-%m-%d %H:%M:%S"},
            "min_body_len": 100,
        },
    },

    # ==========================================================================
    # 정적 HTML + CSS 규칙
    # trafilatura/readability 가 광고·사이드바 노이즈로 본문을 찾지 못하는 경우.
    # 아래 셀렉터는 실제 페이지 HTML 구조 기반이며, 사이트 개편 시 재검증 필요.
    # ==========================================================================

    # ── 매일경제 (25건) ────────────────────────────────────────────────────────
    {
        "host": "www.mk.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1.news_ttl, h2.news_ttl"},
            "body":         {"css": "div.news_cnt_detail_wrap"},
            "author":       {"css": "div.journalist_info strong.name"},
            "published_at": {"css": "dl.journalist_info dd.date, span.registration_time",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 노컷뉴스 (65건) ────────────────────────────────────────────────────────
    {
        "host": "www.nocutnews.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1.title, h2.title"},
            "body":         {"css": "div.article_body, div#article_body"},
            "author":       {"css": "div.writer em"},
            "published_at": {"css": "div.info span.date",
                             "date_format": "%Y-%m-%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 조선비즈 (70건) ────────────────────────────────────────────────────────
    {
        "host": "biz.chosun.com",
        "render_mode": "headless",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # Arc XP Fusion CMS — SSR→CSR 전환 후 정적 fetch 에서 본문 미노출.
        # headless 렌더링 후 section.article-body 에서 추출.
        # 날짜: span.inputDate "입력 2026.06.22. 09:31" → XPath substring-after 로 "입력 " 제거.
        "rules_json": {
            "headless_wait_for": "section.article-body",
            "title":        {"css": "h1.article-header__headline"},
            "body":         {"css": "section.article-body"},
            "author":       {"css": "a.article-byline__author"},
            "published_at": {"xpath": "substring-after(//span[contains(@class,'inputDate')],'입력 ')",
                             "date_format": "%Y.%m.%d. %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 조선일보 (40건) ────────────────────────────────────────────────────────
    # 2026-07 재확인: biz.chosun.com 과 동일하게 SSR→CSR 전환됨 — 정적 fetch 에서
    # article-body 계열 클래스 전부 미노출(trafilatura/readability 도 PARSE_ERROR).
    # headless 렌더링 후 실측 클래스로 교체. published_at: span.inputDate "입력 " 접두어
    # 제거는 biz.chosun.com 룰과 동일 패턴.
    {
        "host": "www.chosun.com",
        "render_mode": "headless",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis-3",
        "rules_json": {
            "headless_wait_for": "section.article-body",
            "title":        {"css": "h1.article-header__headline"},
            "body":         {"css": "section.article-body"},
            "author":       {"css": "a.article-byline__author"},
            "published_at": {"xpath": "substring-after(//span[contains(@class,'inputDate')],'입력 ')",
                             "date_format": "%Y.%m.%d. %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 마이데일리 (105건) ─────────────────────────────────────────────────────
    {
        "host": "www.mydaily.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h3.tit_news, h1.tit_news, h2.tit_news"},
            "body":         {"css": "div.article_txt, div#article_txt, div.news_txt"},
            "author":       {"css": "div.article_info span.name"},
            "published_at": {"css": "div.article_info span.date",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 동아사이언스 (15건) ────────────────────────────────────────────────────
    {
        "host": "www.dongascience.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1.article_title, div.view_top h2"},
            "body":         {"css": "div.article_txt, div.view_content, div.news_view_content"},
            "published_at": {"css": "div.article_info span.date, span.date",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 전남일보 (20건) ────────────────────────────────────────────────────────
    {
        "host": "www.jndn.com",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1.article_title, div.article_head h2, h3.tit"},
            "body":         {"css": "div.article_txt, div#article_body, div.view_txt"},
            "min_body_len": 100,
        },
    },

    # ── 광주일보 (15건) ────────────────────────────────────────────────────────
    {
        "host": "www.kwangju.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1.article_title, div.view_title h2, h3.tit"},
            "body":         {"css": "div.article_txt, div#article_body, div.article_content"},
            "min_body_len": 100,
        },
    },

    # ── 더파워 (15건) ──────────────────────────────────────────────────────────
    {
        "host": "www.thepowernews.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 초기 와일드카드 셀렉터가 오매칭 → og:title + div.gmv2c_con01 로 교체 (실측 기반)
        "rules_json": {
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"css": "div.gmv2c_con01"},
            "min_body_len": 100,
        },
    },

    # ── 데이터뉴스 ────────────────────────────────────────────────────────
    {
        "host": "www.datanews.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"css": "div#news_body_area"},
            "published_at": {"css": "span.datetime", "date_format": "%Y.%m.%d %H:%M:%S"},
            "min_body_len": 100,
        },
    },

    # ── 국토일보 ──────────────────────────────────────────────────────────
    {
        "host": "www.ikld.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 날짜·저자: div.info-text 안 "기자" / "승인 YYYY.MM.DD HH:MM" 텍스트 노드에서 XPath 추출
        "rules_json": {
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"css": "div#article-view-content-div"},
            "author":       {"xpath": "normalize-space((//div[contains(@class,'info-text')]//text()[contains(.,'기자')])[1])"},
            "published_at": {"xpath": "normalize-space(substring-after((//div[contains(@class,'info-text')]//text()[contains(.,'승인')])[1],'승인 '))",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 여성소비자신문 ────────────────────────────────────────────────────
    {
        "host": "www.wsobi.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # EUC-KR 인코딩 사이트 — Content-Type 에 charset 선언 없음.
        # HttpFetcher._decode_response 가 <meta charset=EUC-KR> 감지 후 올바르게 디코딩.
        # 날짜: div#head-info 안 "승인YYYY.MM.DD HH:MM" 텍스트 노드, XPath 로 날짜만 추출.
        "rules_json": {
            "title":        {"css": "span.headline-title"},
            "body":         {"css": "div#articleBody"},
            "published_at": {"xpath": "(//div[@id='head-info']//text()[contains(.,'.') and contains(.,':') and string-length() < 25])[1]",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 엑스포츠뉴스 ─────────────────────────────────────────────────────
    {
        "host": "www.xportsnews.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 날짜: div.at_header 내 "기사입력 YYYY.MM.DD HH:MM" 텍스트 노드에서 XPath substring-after 로 추출
        "rules_json": {
            "title":        {"css": "h1"},
            "body":         {"css": "div.news_contents"},
            "published_at": {"xpath": "substring-after((//text()[contains(.,'기사입력')])[1],'기사입력 ')",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 일간스포츠 (IS+) ──────────────────────────────────────────────────
    {
        "host": "isplus.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 저자: div.journalist_line > p.mr_10 에 "배중현 기자" 형태로 존재
        # 날짜: div.journalist_date 내 <b>등록</b> 다음 text node "YYYY.MM.DD HH:MM"
        "rules_json": {
            "title":        {"css": "p#viewTitle"},
            "body":         {"css": "div#article_body"},
            "author":       {"css": "div.journalist_line p.mr_10"},
            "published_at": {"xpath": "normalize-space(//div[contains(@class,'journalist_date')]//b[normalize-space(.)='등록']/following-sibling::text()[1])",
                             "date_format": "%Y.%m.%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 코리아쉬핑가제트 ──────────────────────────────────────────────────
    {
        "host": "www.ksg.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"xpath": "//meta[@property='og:title']/@content"},
            "body":         {"css": "div#newsContent"},
            "published_at": {"css": "div.subtit", "date_format": "%Y-%m-%d %H:%M"},
            "min_body_len": 100,
        },
    },

    # ── 위클리트레이드 (10건) ──────────────────────────────────────────────────
    {
        "host": "weeklytrade.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1500,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1[class*='title'], h2[class*='title']"},
            "body":         {"css": "div[class*='content'], div[class*='article'], div.view_body"},
            "min_body_len": 100,
        },
    },

    # ── 스카이에디일리 모바일 (5건) ────────────────────────────────────────────
    {
        "host": "m.skyedaily.com",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        "rules_json": {
            "title":        {"css": "h1[class*='title'], h2[class*='title']"},
            "body":         {"css": "div[class*='article'], div[class*='view'], div.news_txt"},
            "min_body_len": 100,
        },
    },

    # ── 프라임경제 (뉴스프라임) ────────────────────────────────────────────────
    {
        "host": "www.newsprime.co.kr",
        "render_mode": "static",
        "crawl_delay_ms": 1000,
        "rules_enabled": True,
        "updated_by": "domain-analysis",
        # 날짜: div.arvdate 마지막 텍스트 노드 = "  |\n<date>" — XPath substring-after 로 파이프 이후만 추출
        "rules_json": {
            "title":        {"css": "div.viewsubject h2.title"},
            "body":         {"css": "div#news_body_area"},
            "author":       {"css": "div.arvdate a span"},
            "published_at": {
                "xpath": "normalize-space(substring-after(//div[@class='arvdate']/text()[last()], '|'))",
                "date_format": "%Y.%m.%d %H:%M:%S",
            },
            "min_body_len": 200,
        },
    },

    # ==========================================================================
    # 소량 실패 (5건 이하) — CSS 규칙 없이 crawl_delay + static 으로 재시도 유도
    # 규칙 추가 전 실제 HTML 구조 확인 후 업데이트 권장
    # ==========================================================================

    {"host": "www.areyou.co.kr",      "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.gndomin.com",       "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.worktoday.co.kr",   "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.techholic.co.kr",   "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.korea.kr",          "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.tennispeople.kr",   "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.stoo.com",          "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.seoul.co.kr",       "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.econotelling.com",  "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "www.doctorstimes.com",  "render_mode": "static", "crawl_delay_ms": 1500,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},
    {"host": "biz.newdaily.co.kr",    "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},

    # www.insight.co.kr: FETCH_CONNECTION(SSL EOF) 반복 발생 — CSS 규칙 문제가
    # 아니라 요청 속도로 인한 WAF/anti-bot 차단으로 추정. crawl_delay_ms 를
    # 전역 기본값(1000ms)보다 넉넉하게 잡아 재발 여부 관찰.
    {"host": "www.insight.co.kr",     "render_mode": "static", "crawl_delay_ms": 2000,
     "rules_enabled": False, "rules_json": None, "updated_by": "domain-analysis"},

    # www.celuvmedia.com: 443 포트 자체가 응답 없음(TLS 핸드셰이크 문제가 아니라
    # HTTPS 서비스 자체 미제공, 전 IP 커넥션 타임아웃 확인). HTTP는 정상 응답.
    # force_http 로 fetch 직전에 스킴을 http 로 강제 다운그레이드.
    {"host": "www.celuvmedia.com",    "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {"force_http": True}},

    # www.msn.com: 본문이 <cp-article> 커스텀 엘리먼트의 open shadow root 안에
    # 있어 page.content() 로는 아예 안 보임(trafilatura/readability 둘 다 빈 결과).
    # headless_with_shadow 로 shadow root 내용을 외부 HTML에 주입 후 CSS로 접근.
    # 한 페이지에 광고 등 shadow root 위젯이 다수(확인 시 14개) 섞여있어
    # div[data-shadow-host='cp-article'] 로 태그명 기준 선택(순회 순서 의존 X).
    # published_at 은 article:published_time 메타가 epoch ms 형태라 현재 룰 엔진의
    # date_format(strptime 전용)으로 못 받아 보류 — 필요 시 rule_engine 에 epoch 지원 추가.
    {"host": "www.msn.com",           "render_mode": "headless_with_shadow", "crawl_delay_ms": 1500,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "headless_wait_for": "cp-article",
         "title":  {"xpath": "//meta[@property='og:title']/@content"},
         "body":   {"css": "div[data-shadow-host='cp-article'] p"},
         "author": {"xpath": "//meta[@property='article:author']/@content"},
         "min_body_len": 200,
     }},

    # www.sisacast.kr: 인증서 만료/hostname mismatch로 HTTPS 접속 불가
    # (curl 확인: "certificate has expired" / 클라이언트에 따라 hostname mismatch로도 나타남).
    # HTTP는 정상 응답 — www.celuvmedia.com과 동일하게 force_http로 처리.
    # 본문은 정적 HTML에 있지만(트래필라투라/readability 둘 다 실패) 구조가
    # www.ikld.kr과 동일한 CMS(info-text 안 "기자"/"승인 날짜" 패턴) — 같은 룰 재사용.
    {"host": "www.sisacast.kr",       "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "force_http": True,
         "title":        {"xpath": "//meta[@property='og:title']/@content"},
         "body":         {"css": "div#article-view-content-div"},
         "author":       {"xpath": "normalize-space((//div[contains(@class,'info-text')]//text()[contains(.,'기자')])[1])"},
         "published_at": {"xpath": "normalize-space(substring-after((//div[contains(@class,'info-text')]//text()[contains(.,'승인')])[1],'승인 '))",
                          "date_format": "%Y.%m.%d %H:%M"},
         "min_body_len": 100,
     }},

    # www.baotintuc.vn (베트남 TTXVN): OpenSSL 3.x가 legacy_renegotiation 이 필요한
    # 서버(구형 ASP.NET/IIS)와의 핸드셰이크를 기본 거부(UNSAFE_LEGACY_RENEGOTIATION_DISABLED).
    # curl은 정상 응답하지만 httpx(OpenSSL 3.x)는 실패 — legacy_renegotiation 규칙으로
    # OP_LEGACY_SERVER_CONNECT를 켠 SSL 컨텍스트를 사용하도록 fetcher에 지시.
    # 본문: div.contents (관련기사/다음기사 위젯을 감싸는 div.content 대신 안쪽만 선택).
    # published_at: meta 값이 ISO 8601 + 베트남 오프셋(+07:00) — %z로 파싱 후 KST 변환.
    {"host": "baotintuc.vn",          "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "legacy_renegotiation": True,
         "title":        {"xpath": "//meta[@property='og:title']/@content"},
         "body":         {"css": "div.contents"},
         "author":       {"css": "div.author"},
         "published_at": {"xpath": "//meta[@property='article:published_time']/@content",
                          "date_format": "%Y-%m-%dT%H:%M:%S%z"},
         "min_body_len": 100,
     }},

    # www.seouleconews.com: 인증서가 완전히 다른 도메인(www.healthinnews.co.kr) 것이 나오고
    # 그마저도 2020년에 만료됨 — 공유호스팅 설정 오류로 추정. HTTPS 자체가 구조적으로 불가하니
    # force_http로 우회 (www.sisacast.kr/www.celuvmedia.com과 동일 패턴).
    # 본문/작성자/날짜 구조는 www.ikld.kr, www.sisacast.kr과 같은 CMS(그누보드 계열)이나
    # info-text가 <li> 로 깔끔히 분리돼 있어 xpath는 li 단위로 지정.
    {"host": "www.seouleconews.com", "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "force_http": True,
         "title":        {"css": "div.article-head-title"},
         "body":         {"css": "div#article-view-content-div"},
         "author":       {"xpath": "normalize-space((//div[contains(@class,'info-text')]//li[contains(.,'기자')])[1])"},
         "published_at": {"xpath": "substring-after(normalize-space((//div[contains(@class,'info-text')]//li[contains(.,'승인')])[1]), '승인 ')",
                          "date_format": "%Y.%m.%d %H:%M"},
         "min_body_len": 100,
     }},

    # www.financialreview.co.kr: www.seouleconews.com과 완전히 동일한 CMS/인증서 문제
    # (다른 도메인 인증서가 나오고 만료됨) — 같은 규칙 그대로 재사용.
    {"host": "www.financialreview.co.kr", "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "force_http": True,
         "title":        {"css": "div.article-head-title"},
         "body":         {"css": "div#article-view-content-div"},
         "author":       {"xpath": "normalize-space((//div[contains(@class,'info-text')]//li[contains(.,'기자')])[1])"},
         "published_at": {"xpath": "substring-after(normalize-space((//div[contains(@class,'info-text')]//li[contains(.,'승인')])[1]), '승인 ')",
                          "date_format": "%Y.%m.%d %H:%M"},
         "min_body_len": 100,
     }},

    # www.fomos.kr: SSL 문제 아님 — trafilatura(favor_precision=True)가 본문 대신
    # byline/날짜(p.sub_tit)만 24자로 뽑아내 body_len 미달로 실패. readability는
    # 정상 추출하지만 library_chain이 trafilatura 결과가 있으면(짧아도) readability로
    # 안 넘어가는 구조라 방치됨. div.view_text(itemprop=articleBody)가 진짜 본문 컨테이너.
    # /redirect/news_view?... 는 /esports/news_view?...로 302 리다이렉트(follow_redirects로 처리됨).
    {"host": "www.fomos.kr", "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "title":        {"xpath": "//meta[@property='og:title']/@content"},
         "body":         {"css": "div.view_text"},
         "author":       {"css": "p.sub_tit span:nth-child(1)"},
         "published_at": {"css": "p.sub_tit span:nth-child(2)", "date_format": "%Y-%m-%d %H:%M"},
         "min_body_len": 100,
     }},

    # autotimes.co.kr: 인증서 체인이 깨져있음(self-signed/issuer 누락) — force_http로 우회.
    # date_repoter 안 span 두 개가 각각 날짜/작성자(순서 고정) — nth 대신 인덱스 xpath로 안전하게 접근.
    # 날짜 형식이 "년/월/일/시/분" 문자 리터럴 포함 — strptime 포맷에 그대로 넣으면 매칭됨.
    {"host": "autotimes.co.kr", "render_mode": "static", "crawl_delay_ms": 1000,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "force_http": True,
         "title":        {"css": "h2.article_title"},
         "body":         {"css": "div#ct"},
         "author":       {"xpath": "normalize-space((//div[contains(@class,'date_repoter')]/span)[2])"},
         "published_at": {"xpath": "substring-after(normalize-space((//div[contains(@class,'date_repoter')]/span)[1]), '입력 ')",
                          "date_format": "%Y년%m월%d일 %H시%M분"},
         "min_body_len": 100,
     }},

    # news.cpbc.co.kr: 순수 CSR — 정적 HTML은 <div id="app-cnbc-front"></div> 빈 셸뿐이라
    # trafilatura/readability 둘 다 PARSE_ERROR. headless_wait_for 를 "#app-cnbc-front *"
    # 처럼 아무 자식이나로 잡으면 GNB(헤더)만 로드된 시점에 캡처돼 본문이 비어있었음
    # (GNB 컴포넌트가 본문보다 먼저 렌더링됨) — 실제 본문 헤더 요소(h3.ah_big_title)가
    # 나타날 때까지 기다리도록 wait_for 셀렉터를 본문 전용으로 지정해 해결.
    {"host": "news.cpbc.co.kr", "render_mode": "headless", "crawl_delay_ms": 1500,
     "rules_enabled": True, "updated_by": "domain-analysis",
     "rules_json": {
         "headless_wait_for": "h3.ah_big_title",
         "title":        {"css": "h3.ah_big_title"},
         "body":         {"css": "div.ab_text.fsize4"},
         "author":       {"css": "span.ahi_name"},
         "published_at": {"xpath": "substring-after(normalize-space(//span[contains(@class,'ahi_date')]), '입력 ')",
                          "date_format": "%Y.%m.%d.%H:%M"},
         "min_body_len": 100,
     }},
]

# ---------------------------------------------------------------------------

_UPSERT_SQL = text("""
    INSERT INTO t_domain
        (host, rules_json, rules_enabled, rules_version,
         render_mode, crawl_delay_ms, updated_by)
    VALUES
        (:host, :rules_json, :rules_enabled, 1,
         :render_mode, :crawl_delay_ms, :updated_by)
    ON DUPLICATE KEY UPDATE
        rules_json        = VALUES(rules_json),
        rules_enabled     = VALUES(rules_enabled),
        rules_version     = VALUES(rules_version),
        render_mode       = VALUES(render_mode),
        crawl_delay_ms    = VALUES(crawl_delay_ms),
        updated_by        = VALUES(updated_by),
        cooldown_until    = NULL,
        recent_fail_count = 0
""")


def main() -> None:
    config.validate()

    print(f"삽입 대상: {len(_RULES)}개 도메인")

    with db_context() as engine:
        with engine.begin() as conn:
            # ON DUPLICATE KEY UPDATE 의 rowcount 는 신규/기존-무변경 여부를 신뢰성 있게
            # 구분하지 못한다(둘 다 1로 나옴). 실행 전 기존 host 집합을 미리 조회해
            # 직접 비교하는 방식으로 INSERT/UPDATE 를 정확히 집계한다.
            existing_hosts = {
                row[0] for row in conn.execute(text("SELECT host FROM t_domain")).fetchall()
            }

            inserted = updated = 0
            for rule in _RULES:
                rules_json = rule.get("rules_json")
                conn.execute(_UPSERT_SQL, {
                    "host":           rule["host"],
                    "rules_json":     json.dumps(rules_json, ensure_ascii=False) if rules_json else None,
                    "rules_enabled":  rule.get("rules_enabled", True),
                    "render_mode":    rule.get("render_mode"),
                    "crawl_delay_ms": rule.get("crawl_delay_ms"),
                    "updated_by":     rule.get("updated_by", "seed"),
                })
                if rule["host"] in existing_hosts:
                    updated += 1
                else:
                    inserted += 1

    print(f"완료: INSERT {inserted}건, UPDATE {updated}건 (총 {inserted + updated}건)")


if __name__ == "__main__":
    main()
