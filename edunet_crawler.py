"""Read-only crawler for Edunet Superintendent-Approved Subjects."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Any

import requests

API_ROOT = "https://api.edunet.net/main/sprApprSbjct"
LIST_URL = f"{API_ROOT}/getSprApprSbjctList"
DETAIL_URL = f"{API_ROOT}/getViewSprApprSbjct"
COMMON_CODE_URL = f"{API_ROOT}/getSprApprSbjctCommonCodeList"

FIELDS = [
    ("과목명", "pstTtl"),
    ("관련교과(교과군)", "relSbj"),
    ("학교급", "pstSchlLvlCd"),
    ("소속(기관)", "pstSchlTypeCd"),
    ("교과서명", "pstTxbNm"),
    ("상태", "aprvYnCd"),
    ("작성일", "regDt"),
]


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


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "EdunetApprovedSubjectsStreamlit/1.0"})
    return session


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


def fetch_code_maps(limiter: RateLimiter | None = None) -> dict[str, dict[str, str]]:
    """Fetch current Korean labels for curriculum, institution, school level, and status codes."""
    limiter = limiter or RateLimiter(0.0)
    payload = _json_request(
        "POST",
        COMMON_CODE_URL,
        json_body=["CRCLM_CD", "LOC_CD", "SLV_CD", "APRV_CD"],
        limiter=limiter,
    )
    data = payload.get("data") or {}
    return {
        group: {str(item.get("comCd")): str(item.get("cdNm") or "") for item in data.get(group, [])}
        for group in ("CRCLM_CD", "LOC_CD", "SLV_CD", "APRV_CD")
    }


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
    if not isinstance(data.get("dataList"), list):
        raise EdunetApiError("목록 API 응답에 dataList가 없습니다.")
    return data


def get_2022_stats(page_size: int = 100) -> dict[str, int]:
    """Return the current record count and page counts using one read-only list request."""
    page_size = max(1, min(int(page_size), 100))
    data = _list_page(1, page_size, 0, RateLimiter(0.0))
    paging = data.get("pagingProperty") or {}
    total_items = int(paging.get("countItem") or 0)
    return {
        "total_items": total_items,
        "api_pages": int(paging.get("countPage") or math.ceil(total_items / page_size) if total_items else 0),
        "site_pages_at_15": math.ceil(total_items / 15) if total_items else 0,
        "page_size": page_size,
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
    """Collect 2022 list rows and visit each selected subject's detail endpoint for textbook name.

    Site pages are 15 rows each. The API accepted up to 100 rows per request in the
    observed session, so the default reduces list calls while preserving every row.
    Detail calls remain one per subject because textbook names are absent from list results.
    """
    page_size = max(1, min(int(page_size), 100))
    workers = max(1, min(int(workers), 5))
    start_page = max(1, int(start_page))
    limiter = RateLimiter(interval_seconds)

    first = _list_page(1, page_size, 0, limiter)
    paging = first.get("pagingProperty") or {}
    total_items = int(paging.get("countItem") or 0)
    total_api_pages = int(paging.get("countPage") or math.ceil(total_items / page_size) if total_items else 0)
    total_site_pages = math.ceil(total_items / 15) if total_items else 0
    if total_api_pages <= 0:
        return CrawlSummary([], [], 0, 0, 0, start_page, start_page)

    last_page = min(total_api_pages, int(end_page) if end_page is not None else total_api_pages)
    if start_page > last_page:
        raise ValueError(f"시작 페이지({start_page})는 종료 페이지({last_page})보다 클 수 없습니다.")

    page_data: list[dict[str, Any]] = []
    api_pages_used = 0
    for page in range(start_page, last_page + 1):
        data = first if page == 1 else _list_page(page, page_size, total_items, limiter)
        page_data.extend(data.get("dataList", []))
        api_pages_used += 1

    # Read the official code labels once, after the list request, to avoid stale hard-coded names.
    codes = fetch_code_maps(limiter)
    items = [(index, item) for index, item in enumerate(page_data)]
    results: dict[int, dict[str, str]] = {}
    errors: list[str] = []

    def fetch_one(index: int, item: dict[str, Any]) -> tuple[int, dict[str, str], str | None]:
        pst_id = item.get("pstId")
        try:
            detail = _detail(pst_id, limiter)
            row = {
                "과목명": _as_text(detail.get("pstTtl") or item.get("pstTtl")),
                "관련교과(교과군)": _as_text(detail.get("relSbj") or item.get("relSbj")),
                "학교급": _label(detail.get("pstSchlLvlCd") or item.get("pstSchlLvlCd"), codes.get("SLV_CD", {})),
                "소속(기관)": _label(detail.get("pstSchlTypeCd") or item.get("pstSchlTypeCd"), codes.get("LOC_CD", {})),
                "교과서명": _as_text(detail.get("pstTxbNm")),
                "상태": _label(detail.get("aprvYnCd") or item.get("aprvYnCd"), codes.get("APRV_CD", {})),
                "작성일": _date_only(detail.get("regDt") or item.get("regDt")),
            }
            return index, row, None
        except Exception as exc:  # Keep list fields even if one detail request fails.
            row = {
                "과목명": _as_text(item.get("pstTtl")),
                "관련교과(교과군)": _as_text(item.get("relSbj")),
                "학교급": _label(item.get("pstSchlLvlCd"), codes.get("SLV_CD", {})),
                "소속(기관)": _label(item.get("pstSchlTypeCd"), codes.get("LOC_CD", {})),
                "교과서명": "",
                "상태": _label(item.get("aprvYnCd"), codes.get("APRV_CD", {})),
                "작성일": _date_only(item.get("regDt")),
            }
            return index, row, f"pstId={pst_id}: {exc}"

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, index, item) for index, item in items]
        for future in as_completed(futures):
            index, row, error = future.result()
            results[index] = row
            completed += 1
            if error:
                errors.append(error)
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
    )
