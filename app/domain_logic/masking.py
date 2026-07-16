"""
텍스트 PII 마스킹 유틸리티.

두 가지 마스킹을 제공한다:
  1. mask_author()  — 저자명 글자 기반 마스킹 (첫글자·끝글자 보존)
  2. TextMasker     — 정규식 패턴 기반 텍스트 마스킹
       - masking_list.json 에서 패턴 로드 (현재 활성 패턴: 전화번호, 이메일 —
         카드번호/주민번호 패턴은 없음)
       - 기자명·특파원명은 callable 내장 패턴으로 처리 (2글자 이름 포함)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 저자명 마스킹
# ---------------------------------------------------------------------------

def mask_author(name: str | None) -> str | None:
    """
    저자명 글자 기반 마스킹.

      1글자 : 그대로
      2글자 : 첫글자 + *
      3글자+: 첫글자 + * × (len-2) + 끝글자
    """
    if not name:
        return name
    name = name.strip()
    if not name:
        return None
    n = len(name)
    if n == 1:
        return name
    if n == 2:
        return name[0] + "*"
    return name[0] + "*" * (n - 2) + name[-1]


# ---------------------------------------------------------------------------
# 텍스트 마스킹 — 패턴 정의
# ---------------------------------------------------------------------------

Replace = Union[str, Callable[[re.Match], str]]


@dataclass(frozen=True)
class _MaskPattern:
    label:   str
    pattern: re.Pattern
    replace: Replace


def _make_title_fn(suffix: str) -> Callable[[re.Match], str]:
    """
    'N글자 이름 [도시명] {suffix}' 형식의 callable 치환 함수 생성.

    이름(첫 단어)만 mask_author() 로 마스킹하고, 도시명과 suffix 는 그대로 보존.
    예) "홍길동 파리 특파원" → "홍*동 파리 특파원"
        "박기 기자"          → "박* 기자"
    """
    suffix_re = re.compile(rf"\s*{re.escape(suffix)}$")

    def _fn(m: re.Match) -> str:
        full = m.group(0)
        without_suffix = suffix_re.sub("", full)
        # 첫 단어 = 이름, 나머지 = 도시·부가 정보
        parts = without_suffix.split(None, 1)
        if not parts:
            return full
        name = parts[0].strip()
        rest = (" " + parts[1].strip()) if len(parts) > 1 else ""
        return (mask_author(name) or name) + rest + f" {suffix}"

    return _fn


# 기자명·특파원명은 글자 수 무관하게 정확히 마스킹 (2글자 이름 포함)
_BUILTIN: list[_MaskPattern] = [
    _MaskPattern(
        label="기자명",
        pattern=re.compile(r"[가-힣]{2,}\s*기자"),
        replace=_make_title_fn("기자"),
    ),
    _MaskPattern(
        label="특파원명",
        pattern=re.compile(r"[가-힣]{2,}(\s+[가-힣]+)?\s*특파원"),
        replace=_make_title_fn("특파원"),
    ),
]


# ---------------------------------------------------------------------------
# TextMasker
# ---------------------------------------------------------------------------

class TextMasker:
    """
    PII 텍스트 마스킹.

    JSON 파일 패턴 (카드번호, 주민번호, 전화번호, 이메일) +
    내장 패턴 (기자명, 특파원명) 을 순서대로 적용한다.

    JSON 패턴 없이도 내장 패턴은 항상 동작한다.

    사용:
        masker = TextMasker().load("masking_list.json")
        clean  = masker.mask(text, label="본문")
    """

    def __init__(self) -> None:
        self._patterns: list[_MaskPattern] = []
        self._loaded  = False
        self._warned  = False

    def load(self, path: str | Path) -> "TextMasker":
        """
        masking_list.json 을 로드한다. 체이닝 가능.

        - 파일 없음: WARNING 후 내장 패턴만 사용
        - JSON 파싱 오류: ERROR 후 내장 패턴만 사용
        - 개별 패턴 오류: WARNING 후 해당 패턴 스킵, 나머지 계속 사용
        """
        path = Path(path)
        if not path.exists():
            _log.warning("masking_list.json 없음: %s — 내장 패턴만 사용", path)
            return self

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            _log.error("masking_list.json 파싱 실패: %s — 내장 패턴만 사용", e)
            return self

        loaded: list[_MaskPattern] = []
        for p in data.get("patterns", []):
            if p.get("use_yn") != "Y":
                continue
            label    = p.get("label", "")
            mask_str = p.get("mask_str", "")
            replace  = p.get("replace_str", "")
            if not mask_str:
                _log.warning("마스킹 패턴 '%s': mask_str 없음 — 스킵", label)
                continue
            try:
                loaded.append(_MaskPattern(
                    label=label,
                    pattern=re.compile(mask_str),
                    replace=replace,
                ))
            except re.error as e:
                _log.warning("마스킹 패턴 '%s' 정규식 오류: %s — 스킵", label, e)

        self._patterns = loaded
        self._loaded = True
        _log.info(
            "마스킹 패턴 로드 완료: 정규식 %d개(카드/주민번호/전화/이메일 등) + 동적 %d개(기자명/특파원명)",
            len(loaded), len(_BUILTIN),
        )
        return self

    def mask(self, text: str, label: str = "") -> str:
        """JSON 패턴 → 내장 패턴 순으로 PII 를 마스킹한다."""
        if not self._loaded and not self._warned:
            _log.warning("TextMasker.load() 미호출 — JSON 패턴 없이 내장 패턴만 적용됩니다")
            self._warned = True
        for p in [*self._patterns, *_BUILTIN]:
            new_text, count = p.pattern.subn(p.replace, text)
            if count > 0:
                _log.debug("[마스킹] %s | %s | %d건", label, p.label, count)
            text = new_text
        return text
