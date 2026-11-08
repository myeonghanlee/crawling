"""Read-only crawler for Edunet Superintendent-Approved Subjects."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import math
import re
import threading
import time
from typing import Any, Callable

import requests

API_ROOT = "https://api.edunet.net/main/sprApprSbjct"
LIST_URL = f"{API_ROOT}/getSprApprSbjctList"
DETAIL_URL = f"{API_ROOT}/getViewSprApprSbjct"
COMMON_CODE_URL = f"{API_ROOT}/getSprApprSbjctCommonCodeList"

# Only fields requested for the result and the list-row ID needed for detail lookup.
LIST_FIELDS = (
    "pstId", "pstTtl", "relSbj", "pstSchlLvlCd", "pstSchlTypeCd",
    "pstTxbNm", "aprvYnCd", "regDt",
)


class EdunetApiError(RuntimeError):
    """Raised when Edunet returns an unexpected or unsuccessful API response."""


class RateLimiter:
    """Space request start times to avoid hammering the source service."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        self.interval = max(0.0, float(interval_seconds))
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_start)
            self._next_start = start_at + self.interval
        delay = start_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)


@dataclass
class CrawlSummary:
    rows: list[dict[str, str]]
    errors: list[str]
    total_items: int
    total_site_pages: int
    api_pages_used: int
    selected_first_page: int
    selected_last_page: int
    course_group_from_detail: int
    course_group_from_existing: int
    course_group_empty: int


def _json_request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    limiter: RateLimiter,
    timeout: tuple[float, float] = (10, 40),
    attempts: int = 4,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        limiter.wait()
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Accept": "application/json", "User-Agent": "EdunetApprovedSubjectsStreamlit/1.0"},
                timeout=timeout,
            )
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 20)
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    continue
            response.raise_for_status()
            try:
                payload = response.json()
            except requests.JSONDecodeError as exc:
                raise EdunetApiError(f"JSON 응답이 아닙니다: {url}") from exc
            if not isinstance(payload, dict) or payload.get("success") is False:
                message = payload.get("message") or payload.get("error_message") or "알 수 없는 API 오류"
                raise EdunetApiError(str(message))
            return payload
        except (requests.RequestException, EdunetApiError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            if isinstance(exc, requests.HTTPError):
                status = exc.response.status_code if exc.response is not None else 0
                if status not in (429, 500, 502, 503, 504):
                    break
            time.sleep(min(1.5 * (2**attempt), 20))
    raise EdunetApiError(str(last_error or "요청에 실패했습니다."))


def _list_page(page: int, page_size: int, total_items: int, limiter: RateLimiter) -> dict[str, Any]:
    params = {
        "currentPage": page,
        "maxResults": page_size,
        "maxLinks": 10,
        "startPage": 1,
        "endPage": 1,
        "countItem": total_items if page > 1 else 0,
        "loading": "false",
        "bbsId": 1,
        "crclmSeCd": "CRCLM01",  # 2022 개정 교육과정
        "pstSchlTypeCd": "",
        "pstSchlLvlCd": "",
        "pstSchlSubLvlCd": "",
        "pstGradLvlCd": "",
        "relSbj": "",
        "pstTtl": "",
        "aprvYnCd": "",
    }
    payload = _json_request("GET", LIST_URL, params=params, limiter=limiter)
    data = payload.get("data") or {}
    raw_items = data.get("dataList")
    if not isinstance(raw_items, list):
        raise EdunetApiError("목록 API 응답에 dataList가 없습니다.")

    # Keep only the seven requested fields and the post ID needed for detail lookup.
    minimal_items = [
        {key: item.get(key) for key in LIST_FIELDS}
        for item in raw_items
        if isinstance(item, dict)
    ]
    return {"pagingProperty": data.get("pagingProperty") or {}, "dataList": minimal_items}


def get_2022_stats(page_size: int = 100) -> dict[str, int]:
    """Return the current 2022 record count and page counts using one list request."""
    page_size = max(1, min(int(page_size), 100))
    data = _list_page(1, page_size, 0, RateLimiter(0.0))
    paging = data.get("pagingProperty") or {}
    total_items = int(paging.get("countItem") or 0)
    api_pages = int(paging.get("countPage") or (math.ceil(total_items / page_size) if total_items else 0))
    return {
        "total_items": total_items,
        "api_pages": api_pages,
        "site_pages_at_15": math.ceil(total_items / 15) if total_items else 0,
        "page_size": page_size,
    }


def fetch_code_maps(limiter: RateLimiter) -> dict[str, dict[str, str]]:
    """Fetch current labels for school level, institution, and approval status codes."""
    payload = _json_request(
        "POST",
        COMMON_CODE_URL,
        json_body=["LOC_CD", "SLV_CD", "APRV_CD"],
        limiter=limiter,
    )
    data = payload.get("data") or {}
    return {
        group: {str(item.get("comCd")): str(item.get("cdNm") or "") for item in data.get(group, [])}
        for group in ("LOC_CD", "SLV_CD", "APRV_CD")
    }


def _detail(pst_id: Any, limiter: RateLimiter) -> dict[str, Any]:
    payload = _json_request(
        "GET",
        DETAIL_URL,
        params={"bbsId": 1, "pstId": pst_id},
        limiter=limiter,
    )
    data = payload.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise EdunetApiError(f"상세 응답 형식이 예상과 다릅니다. pstId={pst_id}")
    return data


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class _BodyTextParser(HTMLParser):
    """Convert detail-body markup into text while preserving field boundaries."""

    _BLOCK_TAGS = {"address", "article", "blockquote", "br", "div", "li", "p", "section", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_tags.append(tag)
            return
        if self._ignored_tags:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            if tag in self._ignored_tags:
                self._ignored_tags.remove(tag)
            return
        if self._ignored_tags:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_tags:
            self.parts.append(data)


def _plain_body_text(content: Any) -> str:
    if content is None:
        return ""
    parser = _BodyTextParser()
    parser.feed(unescape(str(content)))
    text = "".join(parser.parts).replace("\r", "")
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_course_group(content: Any) -> str:
    """Extract the value of item 1, ‘교과(군):’, from a subject's detail body."""
    text = _plain_body_text(content)
    if not text:
        return ""

    label = re.search(r"(?:\b1\s*[.)]\s*|①\s*)?교과\s*\(\s*군\s*\)\s*[:：]\s*", text)
    if not label:
        return ""

    value_tail = text[label.end():].lstrip(" \t\r\n")
    if not value_tail:
        return ""

    next_field = re.search(
        r"\s*(?=(?:[2-9]\s*[.)]\s*|[②-⑨]\s*)|(?:승인\s*허용\s*학교\s*유형|권장\s*이수\s*학점)\s*[:：])",
        value_tail,
    )
    value = value_tail[:next_field.start()] if next_field else value_tail.splitlines()[0]
    return re.sub(r"\s+", " ", value).strip(" \t\r\n:：")


def _date_only(value: Any) -> str:
    text = _as_text(value)
    return text[:10] if len(text) >= 10 else text


def _label(code: Any, mapping: dict[str, str]) -> str:
    value = _as_text(code)
    return mapping.get(value, value)


def crawl_2022_subjects(
    *,
    start_page: int = 1,
    end_page: int | None = None,
    page_size: int = 100,
    workers: int = 2,
    interval_seconds: float = 0.5,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> CrawlSummary:
    """Collect all originally requested fields, enriching related-subject with detail-body item 1."""
    page_size = max(1, min(int(page_size), 100))
    workers = max(1, min(int(workers), 5))
    start_page = max(1, int(start_page))
    limiter = RateLimiter(interval_seconds)

    first = _list_page(1, page_size, 0, limiter)
    paging = first.get("pagingProperty") or {}
    total_items = int(paging.get("countItem") or 0)
    total_api_pages = int(paging.get("countPage") or (math.ceil(total_items / page_size) if total_items else 0))
    total_site_pages = math.ceil(total_items / 15) if total_items else 0
    if total_api_pages <= 0:
        return CrawlSummary([], [], 0, 0, 0, start_page, start_page, 0, 0, 0)

    last_page = min(total_api_pages, int(end_page) if end_page is not None else total_api_pages)
    if start_page > last_page:
        raise ValueError(f"시작 페이지({start_page})는 종료 페이지({last_page})보다 클 수 없습니다.")

    page_data: list[dict[str, Any]] = []
    api_pages_used = 0
    for page in range(start_page, last_page + 1):
        data = first if page == 1 else _list_page(page, page_size, total_items, limiter)
        page_data.extend(data.get("dataList", []))
        api_pages_used += 1

    codes = fetch_code_maps(limiter)
    items = [(index, item) for index, item in enumerate(page_data)]
    results: dict[int, dict[str, str]] = {}
    errors: list[str] = []
    from_detail = 0
    from_existing = 0
    empty = 0
    count_lock = threading.Lock()

    def fetch_one(index: int, item: dict[str, Any]) -> tuple[int, dict[str, str], str | None, str]:
        pst_id = item.get("pstId")
        detail: dict[str, Any] = {}
        error: str | None = None
        try:
            detail = _detail(pst_id, limiter)
        except Exception as exc:
            error = f"pstId={pst_id}: {exc}"

        body_group = _extract_course_group(detail.get("pstCn")) if detail else ""
        old_related = _as_text(detail.get("relSbj") or item.get("relSbj"))
        related = body_group or old_related
        source = "detail" if body_group else ("existing" if old_related else "empty")

        row = {
            "과목명": _as_text(detail.get("pstTtl") or item.get("pstTtl")),
            "관련교과(교과군)": related,
            "학교급": _label(detail.get("pstSchlLvlCd") or item.get("pstSchlLvlCd"), codes.get("SLV_CD", {})),
            "소속(기관)": _label(detail.get("pstSchlTypeCd") or item.get("pstSchlTypeCd"), codes.get("LOC_CD", {})),
            "교과서명": _as_text(detail.get("pstTxbNm") or item.get("pstTxbNm")),
            "상태": _label(detail.get("aprvYnCd") or item.get("aprvYnCd"), codes.get("APRV_CD", {})),
            "작성일": _date_only(detail.get("regDt") or item.get("regDt")),
        }
        return index, row, error, source

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, index, item) for index, item in items]
        for future in as_completed(futures):
            index, row, error, source = future.result()
            results[index] = row
            completed += 1
            if error:
                errors.append(error)
            with count_lock:
                if source == "detail":
                    from_detail += 1
                elif source == "existing":
                    from_existing += 1
                else:
                    empty += 1
            if progress_callback:
                progress_callback(completed, len(items), row.get("과목명", ""))

    rows = [results[index] for index in range(len(items))]
    return CrawlSummary(
        rows=rows,
        errors=errors,
        total_items=total_items,
        total_site_pages=total_site_pages,
        api_pages_used=api_pages_used,
        selected_first_page=start_page,
        selected_last_page=last_page,
        course_group_from_detail=from_detail,
        course_group_from_existing=from_existing,
        course_group_empty=empty,
    )
