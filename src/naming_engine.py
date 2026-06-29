from __future__ import annotations

from pathlib import Path
from collections import Counter
from datetime import datetime
import re

from models import AnalysisRecord, ExtractionResult, FileRecord, NamingResult, build_suggested_path


DATE_PATTERNS_WITH_DAY = [
    # YYYY.MM.DD / YYYY-MM-DD / YYYY년 M월 D일
    re.compile(r"(20\d{2}|19\d{2})[.\-/년\s]+(0?[1-9]|1[0-2])[.\-/월\s]+(0?[1-9]|[12]\d|3[01])"),
    # YYYYMMDD (8자리)
    re.compile(r"(20\d{2}|19\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
    # YY.MM.DD / YY-MM-DD / YY/MM/DD (구분자 있음)
    re.compile(r"\b(\d{2})[.\-/\s]+(0?[1-9]|1[0-2])[.\-/\s]+(0?[1-9]|[12]\d|3[01])\b"),
    # DD.MM.YYYY / DD-MM-YYYY (day-first)
    re.compile(r"\b(0?[1-9]|[12]\d|3[01])[.\-/\s]+(0?[1-9]|1[0-2])[.\-/\s]+(20\d{2}|19\d{2})\b"),
    # YYMMDD (6자리, 구분자 없음) — 반드시 YYYYMM 패턴보다 먼저 시도해야
    # 200303 → yy=20(2020), mm=03, dd=03.  YYYYMMDD(8자리)와 구분: (?!\d)로 뒤 자리 없음 보장
    re.compile(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
]
DATE_PATTERNS_YEAR_MONTH = [
    # Examples: 1993.4월 / 1993-04 / 1993년 4월
    re.compile(r"(20\d{2}|19\d{2})[.\-/년\s]+(0?[1-9]|1[0-2])(?:[.\-/월\s]|$)"),
    # Example: 199304 (YYYYMM)
    re.compile(r"(20\d{2}|19\d{2})(0[1-9]|1[0-2])(?!\d)"),
    # Example: 93.04 / 93-04 (YYMM with separator)
    re.compile(r"\b(\d{2})[.\-/\s]+(0?[1-9]|1[0-2])\b"),
]
DOC_TYPE_KEYWORDS = [
    # 새 8분류 (LLM이 반환하는 값과 일치)
    "소송진행보고", "심리결과보고", "법원명령", "상대방서면", "증거서류", "법률의견", "계약서", "통지서",
    # 기존 세부 유형 (fallback infer_doc_type용)
    "주주간계약서", "소장", "답변서", "준비서면", "의견서", "보고서", "안내문", "안내", "중재신청서",
    "중재판정문", "Partial Award", "Request for Arbitration", "Power of Attorney",
    "Retainer Agreement", "Settlement Agreement", "Statement of Claim", "witness statement",
    "Legal Notice", "Demand Letter", "Invoice", "Memo", "위임장", "품의서", "레터",
]
INSTITUTION_KEYWORDS = [
    "김앤장", "태평양", "광장", "율촌", "화우", "OFAC", "SIAC", "산자부", "법무실", "Pillsbury",
]
SUMMARY_BOILERPLATE_PATTERNS = [
    re.compile(r"^(dear|best regards|regards|sincerely|to whom it may concern)\b", re.IGNORECASE),
    re.compile(r"^(from|to|cc|bcc|subject|sent)\b", re.IGNORECASE),
    re.compile(r"^(첨부|안녕하세요|감사합니다|수신|참조)\b"),
    re.compile(r"any form of notice[, ]+copy", re.IGNORECASE),
    re.compile(r"copying or distribu", re.IGNORECASE),
    re.compile(r"without prior written consent", re.IGNORECASE),
    re.compile(r"all rights reserved", re.IGNORECASE),
]
SUMMARY_KEYWORD_PATTERN = re.compile(
    r"(agreement|contract|arbitration|claim|notice|opinion|invoice|memo|판결|중재|소송|계약|합의|의견|보고)",
    re.IGNORECASE,
)
NOISE_TOKEN_PATTERN = re.compile(r"[\|`~^_=]{2,}|[^\w\s가-힣A-Za-z.,;:!?()\-]{4,}")
FILENAME_STYLE_PREFIX = re.compile(r"^\d{6,8}[_\-\s\[\(]+")
GENERIC_EN_SUMMARY = {
    "preliminary land sale agreement terms",
    "preliminary sale agreement for land",
    "land sale contract between two parties",
}
LOW_VALUE_SUMMARY_PATTERNS = [
    re.compile(r"\b(this is for your information|with reference to the above)\b", re.IGNORECASE),
    re.compile(r"\b(telephone number|tel\.?|fax)\b", re.IGNORECASE),
    re.compile(r"\b(dear|best regards|sincerely)\b", re.IGNORECASE),
]
LOW_VALUE_KEYWORDS = {
    "however", "dear", "reference", "hereinafter", "thereof", "therein",
    "subject", "information", "update", "telephone", "number", "regards",
}
# Company/person/project proper nouns — already captured in case_name or institution fields
COMPANY_PROPER_NOUNS = {
    "daewoo", "dwa", "posco", "sunrise", "nam", "pillsbury",
    "gabbay", "kwanika", "mcc", "rusconi", "dhaheri",
    "international", "corporation", "company", "limited", "llc", "ltd",
    "inc", "corp",
}
KEYWORD_NOISE_PATTERNS = [
    re.compile(r"^page\s*\d+$", re.IGNORECASE),
    re.compile(r"^dear\s+(mr|ms)\.?$", re.IGNORECASE),
    re.compile(r"^however$", re.IGNORECASE),
    re.compile(r"^comm$", re.IGNORECASE),
    re.compile(r"^com$", re.IGNORECASE),
    re.compile(r"^reference\s*no\.?$", re.IGNORECASE),
    re.compile(r"^subject(\s+matter)?$", re.IGNORECASE),
]
KEYWORD_POSITIVE_PATTERN = re.compile(
    r"(legal\s*notice|notice|attachment|arbitration|claim|contract|agreement|"
    r"mou|term\s*sheet|sale|ownership|certificate|land\s*sale|passport|memo)",
    re.IGNORECASE,
)
DOC_TYPE_TITLE_MAP = {
    # 새 8분류 — LLM이 반환하는 값 그대로 문서명으로 사용
    "소송진행보고": "소송진행보고",
    "심리결과보고": "심리결과보고",
    "법원명령": "법원명령",
    "상대방서면": "상대방서면",
    "증거서류": "증거서류",
    "법률의견": "법률의견",
    "통지서": "통지서",
    # 기존 세부 유형 (infer_doc_type fallback 결과 대응)
    "계약서": "계약서",
    "주주간계약서": "주주계약",
    "소장": "소장",
    "답변서": "답변서",
    "준비서면": "준비서면",
    "의견서": "의견서",
    "보고서": "보고",
    "중재신청서": "중재신청",
    "중재판정문": "중재판정",
    "위임장": "위임장",
    "품의서": "품의",
    "양해각서": "양해각서",
    "수정계약서": "수정계약",
    "안내문": "안내문",
    "안내": "안내문",
    "Legal Notice": "법률통지",
    "Demand Letter": "청구통지",
    "Memo": "메모",
    "Power of Attorney": "위임장",
    "Statement of Claim": "청구서",
    "witness statement": "증인진술",
}


def sanitize_filename_component(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip("._")


def strip_folder_status_tag(name: str) -> str:
    """폴더명 앞의 [검토수행], [검토종결] 등 괄호 태그를 제거한다."""
    return re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", name or "").strip()


def _is_boilerplate_sentence(sentence: str) -> bool:
    s = (sentence or "").strip()
    if len(s) < 8:
        return True
    for pattern in SUMMARY_BOILERPLATE_PATTERNS:
        if pattern.search(s):
            return True
    return False


def _is_garbled_english(sentence: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", sentence)
    if len(words) < 3:
        return False
    no_vowel = sum(1 for w in words if not re.search(r"[aeiouAEIOU]", w))
    if (no_vowel / len(words)) >= 0.4:
        return True
    short_words = sum(1 for w in words if len(w) <= 3)
    if (short_words / len(words)) >= 0.7:
        return True
    return False


def _looks_like_filename_phrase(sentence: str) -> bool:
    s = (sentence or "").strip()
    if not s:
        return False
    if FILENAME_STYLE_PREFIX.search(s):
        return True
    if re.search(r"\[[^\]]{2,30}\]", s) and re.search(r"\d{6,8}", s):
        return True
    return False


def _normalize_summary_seed(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    if not s:
        return ""
    s = FILENAME_STYLE_PREFIX.sub("", s).strip()
    s = re.sub(r"\[[^\]]{0,40}\]", "", s).strip()
    s = re.sub(r"\(\d{4}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?\)", "", s).strip()
    return re.sub(r"\s+", " ", s).strip()


def _contains_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


def _pick_korean_phrase(text: str, target_len: int = 28) -> str:
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", re.sub(r"\s+", " ", text or "").strip())
    best = ""
    best_score = -1
    for sentence in sentences:
        s = sentence.strip()
        if not s or not _contains_korean(s):
            continue
        if _is_boilerplate_sentence(s) or _looks_like_noise_sentence(s):
            continue
        core = re.sub(r"[^가-힣A-Za-z0-9\s]", "", s).strip()
        if not core:
            continue
        korean_count = len(re.findall(r"[가-힣]", core))
        if korean_count < 6:
            continue
        score = korean_count + max(0, 20 - abs(len(core) - target_len))
        if score > best_score:
            best_score = score
            best = core
    return best


def _summary_is_acceptable(summary: str, prefer_korean: bool) -> bool:
    s = re.sub(r"\s+", " ", summary or "").strip().rstrip(".")
    if len(s) < 8:
        return False
    if re.search(r"\b\d{4,}\b|\+\d{1,3}\s?\d", s):
        return False
    if _looks_like_filename_phrase(s) or _looks_like_noise_sentence(s) or _is_garbled_english(s):
        return False
    for pattern in LOW_VALUE_SUMMARY_PATTERNS:
        if pattern.search(s):
            return False
    if re.search(r"\b(subject matter claim|with reference|for your information)\b", s, re.IGNORECASE):
        return False
    if prefer_korean and not _contains_korean(s):
        return False
    return True


def _is_useful_keyword(keyword: str) -> bool:
    k = (keyword or "").strip()
    if not k:
        return False
    lower = k.lower()
    if lower in LOW_VALUE_KEYWORDS:
        return False
    if lower in COMPANY_PROPER_NOUNS:
        return False
    if re.search(r"\b(?:%s)\b" % "|".join(re.escape(x) for x in LOW_VALUE_KEYWORDS), lower):
        return False
    if len(re.findall(r"[A-Za-z]", k)) >= 4 and not re.search(r"(agreement|contract|claim|notice|arbitration|opinion|certificate|payment|litigation|counsel|hearing|dissolution|indemnity|guarantee|standstill|attachment|consolidation)", lower):
        return False
    return True


def _normalize_keyword_token(token: str) -> str:
    t = sanitize_filename_component(token or "")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    t = re.sub(r"^page\s*\d+\s*$", "", t, flags=re.IGNORECASE).strip()
    if not t:
        return ""
    lower = t.lower()
    if lower in LOW_VALUE_KEYWORDS:
        return ""
    if lower in COMPANY_PROPER_NOUNS:
        return ""
    # 구문 내 개별 단어 중 하나라도 회사/고유명사 차단 목록이면 제거
    words = re.findall(r"[A-Za-z]+", lower)
    if words and all(w in COMPANY_PROPER_NOUNS for w in words):
        return ""
    for pattern in KEYWORD_NOISE_PATTERNS:
        if pattern.search(t):
            return ""
    # Remove tokens that are mostly digits/symbols.
    alpha = len(re.findall(r"[A-Za-z]", t))
    if alpha < 3:
        return ""
    # Reject sentence fragments (more than 6 words = not a keyword)
    if len(t.split()) > 6:
        return ""
    return t


def _build_structured_summary(doc_type: str, case_name: str, institution: str, keyword: str, max_len: int = 80) -> str:
    parts: list[str] = []
    if case_name:
        parts.append(case_name)
    if institution:
        parts.append(institution)
    if doc_type:
        parts.append(doc_type)
    if keyword and _is_useful_keyword(keyword):
        parts.append(keyword)
    if not parts:
        parts.append("문서")
    # Build a more descriptive summary rather than just "X 관련 문서"
    if len(parts) == 1 and parts[0] == "문서":
        text = "법률 문서"
    elif doc_type and (case_name or institution):
        context = case_name or institution
        text = f"{context} {doc_type}"
        if keyword and _is_useful_keyword(keyword):
            text += f" - {keyword}"
    else:
        text = " ".join(parts) + " 관련 문서"
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= max_len:
        text = text[: max_len - 1].rstrip()
    return text.rstrip(" .!?。！？,;:") + "."


def _looks_like_noise_sentence(sentence: str) -> bool:
    s = (sentence or "").strip()
    if not s:
        return True
    if NOISE_TOKEN_PATTERN.search(s):
        return True
    alnum = re.findall(r"[A-Za-z가-힣0-9]", s)
    if len(alnum) < 10:
        return True
    symbol_count = len(re.findall(r"[^A-Za-z가-힣0-9\s.,;:!?()\-]", s))
    if symbol_count > 0 and (symbol_count / max(1, len(s))) >= 0.2:
        return True
    tokens = re.findall(r"[A-Za-z가-힣0-9]+", s)
    if tokens:
        one_or_two = sum(1 for t in tokens if len(t) <= 2)
        if (one_or_two / len(tokens)) >= 0.5:
            return True
    return False


def _clean_text_for_summary(text: str) -> str:
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not raw:
        return ""
    candidates = re.split(r"(?<=[.!?。！？])\s+|\n+", raw)
    cleaned: list[str] = []
    for sentence in candidates:
        s = re.sub(r"\s+", " ", sentence).strip()
        if not s:
            continue
        if _is_boilerplate_sentence(s):
            continue
        if _is_garbled_english(s):
            continue
        if _looks_like_noise_sentence(s):
            continue
        cleaned.append(s)
    return " ".join(cleaned).strip()


def _summary_source_quality(text: str) -> float:
    s = re.sub(r"\s+", " ", text or "").strip()
    if not s:
        return 0.0
    valid = len(re.findall(r"[A-Za-z가-힣0-9\s.,;:!?()\-]", s))
    ratio = valid / max(1, len(s))
    garbled_hits = len(re.findall(r"\b[a-z]{1,2}\b|[A-Za-z]{8,}[0-9]{2,}|[0-9]{5,}", s))
    penalty = min(0.5, garbled_hits / 40.0)
    return max(0.0, min(1.0, ratio - penalty))


def _pick_best_sentence_from_text(text: str, target_len: int = 34, prefer_korean: bool = False) -> str:
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not raw:
        return ""
    candidates = re.split(r"(?<=[.!?。！？])\s+|\n+", raw)
    scored: list[tuple[int, int, int, int, str]] = []
    seen_norm: set[str] = set()
    for sentence in candidates:
        s = re.sub(r"\s+", " ", sentence).strip()
        if not s or _is_boilerplate_sentence(s) or _is_garbled_english(s):
            continue
        norm_key = re.sub(r"[^a-z0-9가-힣]+", "", s.lower())
        if not norm_key or norm_key in seen_norm:
            continue
        seen_norm.add(norm_key)
        keyword_score = 2 if SUMMARY_KEYWORD_PATTERN.search(s) else 0
        length_score = min(len(s), 80)
        length_fit = max(0, 40 - abs(len(s) - target_len))
        structure_score = 1 if re.search(r"\b(this|agreement|contract|claim|arbitration)\b", s, re.IGNORECASE) else 0
        korean_bonus = 3 if prefer_korean and _contains_korean(s) else 0
        scored.append((korean_bonus + keyword_score, structure_score, length_fit, length_score, s))
    if scored:
        scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        return scored[0][4]
    return ""


def normalize_summary(
    summary: str,
    fallback_text: str,
    fallback_name: str,
    max_len: int = 40,
    min_len: int = 24,
    prefer_filename_fallback: bool = False,
) -> str:
    llm_summary = _normalize_summary_seed(summary)
    cleaned_fallback = _clean_text_for_summary(fallback_text)
    prefer_korean = _contains_korean(cleaned_fallback) or _contains_korean(fallback_name)
    base = ""
    if (
        llm_summary
        and not _is_boilerplate_sentence(llm_summary)
        and not _is_garbled_english(llm_summary)
        and not _looks_like_filename_phrase(llm_summary)
        and (not prefer_korean or _contains_korean(llm_summary))
    ):
        base = llm_summary
    if not base:
        base = _pick_best_sentence_from_text(cleaned_fallback, target_len=max(30, max_len - 6), prefer_korean=prefer_korean)
    if not base and prefer_korean:
        base = _pick_korean_phrase(cleaned_fallback, target_len=max(24, max_len - 8))
    if not base:
        base = re.sub(r"\s+", " ", cleaned_fallback or "").strip()
    if not base:
        base = sanitize_filename_component(Path(fallback_name).stem)

    base = re.sub(r"\s+", " ", base).strip()
    sentence_split = re.split(r"(?<=[.!?。！？])\s+", base)
    first_sentence = sentence_split[0].strip() if sentence_split else base
    if not first_sentence:
        first_sentence = base

    first_sentence = re.sub(r"\s+", " ", first_sentence).strip()
    if len(first_sentence) < min_len and fallback_text:
        # Keep one sentence style but enrich with an additional informative fragment.
        extra = _pick_best_sentence_from_text(cleaned_fallback, target_len=max_len, prefer_korean=prefer_korean)
        if extra and extra != first_sentence:
            combined = f"{first_sentence} {extra}"
            first_sentence = re.split(r"(?<=[.!?。！？])\s+", combined)[0].strip() or combined.strip()

    if len(first_sentence) > max_len:
        first_sentence = first_sentence[:max_len].rstrip()

    if not first_sentence:
        first_sentence = "요약없음"

    # Force a period at sentence end.
    first_sentence = first_sentence.rstrip(" .!?。！？,;:")
    if len(first_sentence) >= max_len:
        first_sentence = first_sentence[: max_len - 1].rstrip()
    return f"{first_sentence}."


def _normalize_document_title(title: str, max_chars: int = 10) -> str:
    title = re.sub(r"\s+", "", title or "")
    if not title:
        return "문서"
    normalized_chars: list[str] = []
    for ch in title:
        if re.match(r"[A-Za-z가-힣]", ch):
            normalized_chars.append(ch)
    out = "".join(normalized_chars).strip()
    out = re.sub(r"(.)\1{2,}", r"\1\1", out)
    if not out:
        return "문서"
    return out[:max_chars]


def _mapped_doc_title(doc_type: str) -> str:
    if not doc_type:
        return ""
    source = (doc_type or "").lower()
    for key, mapped in DOC_TYPE_TITLE_MAP.items():
        if key.lower() in source:
            return _normalize_document_title(mapped, max_chars=10)
    return ""


def _title_from_summary_text(summary: str, max_chars: int = 10) -> str:
    """단어 경계를 지키면서 max_chars 이내의 제목을 생성한다."""
    cleaned = re.sub(r"[^A-Za-z가-힣\s]", "", summary or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    result = ""
    for word in words:
        candidate = result + word
        if len(candidate) <= max_chars:
            result = candidate
        else:
            break
    if not result:
        result = words[0][:max_chars]
    return result


def _document_title_from_summary(summary: str, doc_type: str) -> str:
    mapped = _mapped_doc_title(doc_type)
    if mapped:
        return mapped
    if summary:
        title = _title_from_summary_text(summary, max_chars=10)
        if title:
            return title
    return "문서"


def _normalize_two_digit_year(two_digit_year: str) -> int:
    year = int(two_digit_year)
    # 00~39 => 2000s, 40~99 => 1900s
    return 2000 + year if year <= 39 else 1900 + year


def _yymmdd_to_year(yymmdd: str) -> int | None:
    """YYMMDD 6자리 문자열에서 4자리 연도를 반환한다."""
    m = re.match(r"^(\d{2})\d{4}$", (yymmdd or "").strip())
    if not m:
        return None
    return _normalize_two_digit_year(m.group(1))


def _format_yymmdd(year: int, month: int, day: int) -> str:
    try:
        validated = datetime(year, month, day)
        return validated.strftime("%y%m%d")
    except ValueError:
        return ""


def _normalize_llm_date(raw: str) -> str:
    """LLM이 반환한 날짜 문자열을 항상 YYMMDD 6자리로 정규화한다.

    LLM이 규칙을 어기고 YYYYMMDD(8자리)나 YYYY-MM-DD 형식을 반환하는 경우를 처리.
    """
    s = re.sub(r"[.\-/년월일\s]", "", (raw or "").strip())
    if not s or s.lower() == "date_unknown":
        return "date_unknown"
    # YYYYMMDD (8자리) → YYMMDD
    m = re.match(r"^((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$", s)
    if m:
        result = _format_yymmdd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if result:
            return result
    # YYYYMM (6자리, 19/20 시작) → YYMMday=1
    m = re.match(r"^((?:19|20)\d{2})(0[1-9]|1[0-2])$", s)
    if m:
        result = _format_yymmdd(int(m.group(1)), int(m.group(2)), 1)
        if result:
            return result
    # 이미 YYMMDD (6자리, 19/20 미시작)
    m = re.match(r"^(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$", s)
    if m:
        result = _format_yymmdd(_normalize_two_digit_year(m.group(1)), int(m.group(2)), int(m.group(3)))
        if result:
            return result
    # YYMM (4자리) → day=1
    m = re.match(r"^(\d{2})(0[1-9]|1[0-2])$", s)
    if m:
        result = _format_yymmdd(_normalize_two_digit_year(m.group(1)), int(m.group(2)), 1)
        if result:
            return result
    # 그 외 형식은 normalize_date에 위임
    return normalize_date(raw)


def normalize_date(text: str) -> str:
    source = text or ""
    for idx, pattern in enumerate(DATE_PATTERNS_WITH_DAY):
        match = pattern.search(source)
        if not match:
            continue
        if idx in (0, 1):  # YYYY-based
            year, month, day = match.groups()
            result = _format_yymmdd(int(year), int(month), int(day))
            if result:
                return result
        elif idx == 2:  # YY.MM.DD
            yy, month, day = match.groups()
            result = _format_yymmdd(_normalize_two_digit_year(yy), int(month), int(day))
            if result:
                return result
        elif idx == 3:  # DD.MM.YYYY
            day, month, year = match.groups()
            result = _format_yymmdd(int(year), int(month), int(day))
            if result:
                return result
        elif idx == 4:  # YYMMDD (6자리, 구분자 없음)
            yy, month, day = match.groups()
            result = _format_yymmdd(_normalize_two_digit_year(yy), int(month), int(day))
            if result:
                return result

    for idx, pattern in enumerate(DATE_PATTERNS_YEAR_MONTH):
        match = pattern.search(source)
        if not match:
            continue
        if idx in (0, 1):  # YYYY-MM
            year, month = match.groups()
            result = _format_yymmdd(int(year), int(month), 1)
            if result:
                return result
        elif idx == 2:  # YY-MM
            yy, month = match.groups()
            result = _format_yymmdd(_normalize_two_digit_year(yy), int(month), 1)
            if result:
                return result
    return "date_unknown"


def infer_doc_type(text: str, fallback_name: str) -> str:
    source = f"{fallback_name}\n{text[:2000]}"
    for keyword in DOC_TYPE_KEYWORDS:
        if keyword.lower() in source.lower():
            return keyword
    return ""


def infer_institution(text: str, fallback_name: str) -> str:
    source = f"{fallback_name}\n{text[:1500]}"
    for keyword in INSTITUTION_KEYWORDS:
        if keyword.lower() in source.lower():
            return keyword
    return ""


def infer_case_name(file_record: FileRecord) -> str:
    """사건명/프로젝트명은 항상 폴더 트리의 3번째 레벨에서 가져온다.

    relative_path_from_root 기준 (root_folder 제외 후):
      parts[0] = 대분류 (예: 소송 및 중재)
      parts[1] = 사건명  (예: 두바이 Sunrise 소송)  ← 여기서 가져옴
      parts[2] = 소분류 (예: 1. 사건기록)
      ...
    depth가 부족하면 가장 가까운 상위 폴더를 사용한다.
    """
    folder_parts = [p for p in Path(file_record.relative_path_from_root).parts[:-1]]
    if len(folder_parts) >= 2:
        return sanitize_filename_component(strip_folder_status_tag(folder_parts[1]))
    if len(folder_parts) == 1:
        return sanitize_filename_component(strip_folder_status_tag(folder_parts[0]))
    return ""


def infer_keyword(text: str, file_record: FileRecord) -> str:
    source = f"{file_record.original_file_name}\n{text[:12000]}"
    source = re.sub(r"PAGE\s+\d+", " ", source, flags=re.IGNORECASE)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\- ]{2,40}", source)
    normalized: list[str] = []
    for token in tokens:
        t = _normalize_keyword_token(token)
        if t:
            normalized.append(t)
    if not normalized:
        return ""
    counts = Counter(normalized)
    ranked: list[tuple[int, int, str]] = []
    for token, count in counts.items():
        positive = 2 if KEYWORD_POSITIVE_PATTERN.search(token) else 0
        ranked.append((positive, count, token))
    ranked.sort(key=lambda item: (item[0], item[1], len(item[2])), reverse=True)
    best = ranked[0][2] if ranked else ""
    return sanitize_filename_component(best)[:40]


def _keyword_supported_by_text(keyword: str, text: str) -> bool:
    k = _normalize_keyword_token(keyword)
    if not k:
        return False
    t = (text or "").lower()
    if not t:
        return False
    compact = re.sub(r"\s+", " ", k.lower()).strip()
    if compact in t:
        return True
    # Also allow partial token support for compound keywords.
    parts = [p for p in re.split(r"\s+", compact) if len(p) >= 4]
    if not parts:
        return False
    hits = sum(1 for p in parts if p in t)
    return hits >= max(1, len(parts) // 2)


def build_filename(org_name: str, document_title: str, case_name: str, institution: str, document_date: str, english_keyword: str, extension: str) -> str:
    # 패턴: 법무실_사건명_문서명_[키워드_]날짜
    # 사건명(case_name)은 항상 2번째 자리 고정 (institution으로 대체하지 않음)
    parts = [org_name]
    if case_name and case_name != org_name:
        parts.append(case_name)
    parts.append(document_title or "문서")
    if english_keyword:
        parts.append(english_keyword)
    parts.append(document_date or "date_unknown")
    cleaned = [sanitize_filename_component(part) for part in parts if sanitize_filename_component(part)]
    return "_".join(cleaned) + extension


def _extraction_status_label(status: str) -> str:
    if status in ("empty_text", "ocr_empty_text"):
        return "텍스트 추출 결과 없음"
    if status.startswith("missing_dependency"):
        return "필수 라이브러리 미설치"
    if status == "insufficient_text_no_ocr":
        return "텍스트 부족 (OCR 미실행)"
    if status == "manual_review_required":
        return "수동 검토 필요 형식"
    if status.startswith(("error:", "worker_error:")):
        return "추출 오류"
    if status.startswith("advanced_ocr:"):
        return "OCR 처리 실패"
    return f"추출 실패 ({status})"


def evaluate_manual_review(extraction: ExtractionResult, result: NamingResult, threshold: float) -> tuple[bool, str]:
    if extraction.extraction_status != "success":
        return True, _extraction_status_label(extraction.extraction_status)
    if extraction.ocr_quality_low:
        return True, "OCR 품질 낮음"
    if not result.extracted_doc_type:
        return True, "문서 유형 미확인"
    if not result.extracted_document_title:
        return True, "문서 제목 미확인"
    if result.extracted_date == "date_unknown":
        return True, "날짜 미확인"
    if result.confidence < threshold:
        return True, f"신뢰도 미달 ({round(result.confidence * 100)}% < {round(threshold * 100)}%)"
    if result.conflict_detected:
        return True, "파일명 중복"
    return False, ""


def propose_name(file_record: FileRecord, extraction: ExtractionResult, llm_result: dict | None, config: dict, llm_client=None) -> NamingResult:
    threshold = float(config["naming"].get("confidence_threshold", 0.85))
    max_filename_length = int(config["naming"].get("max_filename_length", 180))

    doc_type = sanitize_filename_component(str((llm_result or {}).get("doc_type") or infer_doc_type(extraction.text_excerpt, file_record.original_file_name)))
    # 사건명은 항상 폴더 구조에서 가져온다 (LLM 결과 무시)
    case_name = infer_case_name(file_record)
    institution = sanitize_filename_component(str((llm_result or {}).get("institution_or_lawfirm") or infer_institution(extraction.text_excerpt, file_record.original_file_name)))
    # 날짜: LLM 결과 우선, YYYYMMDD 등 비정상 포맷은 YYMMDD로 정규화
    filename_date = normalize_date(file_record.original_file_name)
    llm_date_raw = str((llm_result or {}).get("document_date") or "").strip()
    if llm_date_raw:
        extracted_date = sanitize_filename_component(_normalize_llm_date(llm_date_raw))
        # LLM 날짜와 파일명 날짜 연도 차이가 5년 초과이면 파일명 날짜를 신뢰
        # (LLM이 본문의 오래된 참조 날짜를 발행일로 잘못 읽는 케이스 방어)
        if (
            extracted_date and extracted_date != "date_unknown"
            and filename_date and filename_date != "date_unknown"
        ):
            llm_yr = _yymmdd_to_year(extracted_date)
            fn_yr = _yymmdd_to_year(filename_date)
            if llm_yr and fn_yr and abs(llm_yr - fn_yr) > 5:
                extracted_date = filename_date
    else:
        extracted_date = sanitize_filename_component(normalize_date(f"{file_record.original_file_name}\n{extraction.text_excerpt}"))
    if not extracted_date:
        extracted_date = "date_unknown"

    keyword_source_text = extraction.extracted_text or extraction.text_excerpt
    llm_keyword = _normalize_keyword_token(str((llm_result or {}).get("english_keyword") or ""))
    kw_confidence_threshold = float(config.get("ocr", {}).get("keyword_confidence_threshold", 75.0))
    ocr_confidence_too_low = (
        extraction.ocr_used
        and extraction.ocr_mean_confidence > 0.0
        and extraction.ocr_mean_confidence < kw_confidence_threshold
    )
    if ocr_confidence_too_low:
        keyword = ""
    elif llm_keyword:
        # LLM이 키워드를 제공했으면 본문 지지 여부와 관계없이 우선 사용
        # (LLM은 실제 문서 내용을 읽었으므로 신뢰)
        keyword = sanitize_filename_component(llm_keyword)
    else:
        # LLM 키워드 없을 때만 frequency 기반 추출
        inferred_keyword = infer_keyword(keyword_source_text, file_record)
        keyword = sanitize_filename_component(inferred_keyword)

    raw_summary = sanitize_filename_component(str((llm_result or {}).get("summary") or ""))
    source_quality = _summary_source_quality(extraction.text_excerpt)

    # If the LLM provided a summary, validate it leniently (ignore prefer_korean —
    # the LLM read the actual document and its language choice should be trusted).
    llm_summary_accepted = False
    if raw_summary:
        normalized_llm = _normalize_summary_seed(raw_summary)
        if (
            normalized_llm
            and not _is_boilerplate_sentence(normalized_llm)
            and not _is_garbled_english(normalized_llm)
            and not _looks_like_filename_phrase(normalized_llm)
            and not _looks_like_noise_sentence(normalized_llm)
            and len(normalized_llm) >= 8
        ):
            summary = normalized_llm.rstrip(" .!?。！？,;:") + "."
            if len(summary) > 80:
                summary = summary[:79].rstrip() + "."
            llm_summary_accepted = True

    if not llm_summary_accepted:
        # Fall back to extracting the best sentence from the document text.
        summary = normalize_summary(
            "",
            extraction.text_excerpt,
            file_record.original_file_name,
            max_len=80,
            min_len=24,
            prefer_filename_fallback=False,
        )
        prefer_korean_summary = _contains_korean(extraction.text_excerpt) or _contains_korean(case_name) or _contains_korean(institution)
        if not _summary_is_acceptable(summary, prefer_korean=prefer_korean_summary):
            summary = _build_structured_summary(doc_type, case_name, institution, keyword, max_len=80)

    llm_title = sanitize_filename_component(str((llm_result or {}).get("document_title") or ""))
    mapped_title = _mapped_doc_title(doc_type)
    # document_title 우선 — doc_type 대분류보다 이 문서의 구체적 특성을 반영
    if llm_title and re.match(r"^[A-Za-z가-힣0-9\s\-]{2,20}$", llm_title):
        document_title = _normalize_document_title(llm_title, max_chars=10)
    elif mapped_title:
        document_title = mapped_title
    else:
        # ③ 최후 수단: LLM이 있으면 summary에서 제목 재생성 요청, 없으면 프로그래매틱 추출
        if llm_client is not None and summary:
            generated = llm_client.generate_short_title(summary)
            if generated:
                document_title = _normalize_document_title(generated, max_chars=10)
            else:
                document_title = _document_title_from_summary(summary, doc_type)
        else:
            document_title = _document_title_from_summary(summary, doc_type)
    if document_title == "문서" and doc_type:
        document_title = _document_title_from_summary("", doc_type)
    if not doc_type and not mapped_title and not llm_title:
        document_title = "관련문서"

    raw_abstract = str((llm_result or {}).get("document_abstract") or "").strip()

    result = NamingResult(
        extracted_summary=summary,
        document_abstract=raw_abstract,
        extracted_document_title=document_title,
        extracted_doc_type=doc_type,
        extracted_case_name=case_name,
        extracted_institution=institution,
        extracted_date=extracted_date,
        extracted_keyword=keyword,
        reason=sanitize_filename_component(str((llm_result or {}).get("reason") or "")),
        confidence=float((llm_result or {}).get("confidence") or 0.72),
    )
    if source_quality < 0.55:
        result.confidence = min(result.confidence, 0.82)
    result.suggested_file_name = build_filename(
        org_name=config["naming"].get("org_name", "법무실"),
        document_title=result.extracted_document_title,
        case_name=result.extracted_case_name,
        institution=result.extracted_institution,
        document_date=result.extracted_date or "date_unknown",
        english_keyword=result.extracted_keyword,
        extension=file_record.file_extension,
    )
    if len(result.suggested_file_name) > max_filename_length:
        stem = Path(result.suggested_file_name).stem[: max_filename_length - len(file_record.file_extension)]
        result.suggested_file_name = f"{stem}{file_record.file_extension}"
        result.reason = f"{result.reason} 파일명 길이를 제한에 맞게 잘랐습니다.".strip()
        result.confidence = min(result.confidence, 0.8)
    result.suggested_full_path = build_suggested_path(file_record.original_dir_path, result.suggested_file_name)
    needs_review, review_reason = evaluate_manual_review(extraction, result, threshold)
    result.needs_manual_review = needs_review
    if needs_review and review_reason:
        prefix = result.reason.strip()
        result.reason = f"{prefix} [{review_reason}]".strip() if prefix else f"[{review_reason}]"
    result.rollback_name = file_record.original_file_name
    return result


def mark_conflicts(records: list[AnalysisRecord]) -> None:
    summary_counter = Counter(
        (record.naming.extracted_summary or "").strip().lower().rstrip(".")
        for record in records
        if (record.naming.extracted_summary or "").strip()
    )
    for record in records:
        summary_key = (record.naming.extracted_summary or "").strip().lower().rstrip(".")
        if summary_counter.get(summary_key, 0) > 1 and summary_key in GENERIC_EN_SUMMARY:
            case_or_inst = record.naming.extracted_case_name or record.naming.extracted_institution
            if case_or_inst:
                merged = f"{case_or_inst} {record.naming.extracted_summary}".strip()
                merged = re.sub(r"\s+", " ", merged)
                if len(merged) > 40:
                    merged = merged[:39].rstrip() + "."
                elif not merged.endswith("."):
                    merged = merged.rstrip(" .!?。！？,;:") + "."
                record.naming.extracted_summary = merged

    # Group records by suggested_full_path (case-insensitive) to detect duplicates.
    from collections import defaultdict
    path_groups: dict[str, list[AnalysisRecord]] = defaultdict(list)
    for record in records:
        if record.naming.suggested_full_path:
            path_groups[record.naming.suggested_full_path.lower()].append(record)

    for group in path_groups.values():
        if len(group) <= 1:
            continue
        # Assign serial numbers (1), (2), ... before the extension to make each unique.
        for idx, record in enumerate(group, start=1):
            stem = Path(record.naming.suggested_file_name).stem
            ext = Path(record.naming.suggested_file_name).suffix
            new_name = f"{stem}({idx}){ext}"
            record.naming.suggested_file_name = new_name
            record.naming.suggested_full_path = build_suggested_path(
                record.file_record.original_dir_path, new_name
            )
            record.naming.conflict_detected = True
            record.naming.rename_status = "duplicate_conflict"
            record.naming.reason = f"{record.naming.reason} 동일 후보 파일명 충돌 - 일련번호({idx}) 부여됨.".strip()
            record.naming.confidence = min(record.naming.confidence, 0.84)
            record.naming.needs_manual_review = True
