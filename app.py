from __future__ import annotations

from datetime import datetime
import math

import pandas as pd
import streamlit as st

from edunet_crawler import crawl_2022_subjects, get_2022_stats

st.set_page_config(
    page_title="에듀넷 교육감 승인과목 수집기",
    page_icon="📚",
    layout="wide",
)

st.title("에듀넷 교육감 승인과목 수집기")
st.caption("2022 개정 교육과정의 과목 목록과 상세 화면의 교과서명을 모읍니다.")

st.info(
    "이 도구는 에듀넷 화면이 사용하는 읽기 전용 API를 조회합니다. "
    "아이디·비밀번호를 입력하거나 저장하지 않습니다. 현재 확인한 API는 로그인 쿠키 없이도 "
    "목록과 상세 데이터를 반환했습니다. 사이트 정책을 확인하고, 요청 간격은 기본 0.5초로 유지하세요."
)

st.markdown(
    "**수집 항목:** 과목명 · 관련교과(교과군) · 학교급 · 소속(기관) · 교과서명 · 상태 · 작성일  "
    "\n\n교과서명은 목록 API에 없고 상세 API에서만 확인되어, 선택한 각 과목마다 상세 조회가 한 번씩 추가됩니다."
)

@st.cache_data(ttl=300, show_spinner=False)
def cached_stats(page_size: int) -> dict[str, int]:
    return get_2022_stats(page_size)


page_size = st.selectbox(
    "API 한 번에 가져올 목록 수",
    options=[15, 50, 100],
    index=2,
    help="에듀넷 화면은 15건씩 표시합니다. API는 최대 100건 요청을 허용해 기본값을 100으로 설정했습니다.",
)

try:
    with st.spinner("현재 2022 교육과정 자료 수를 확인합니다…"):
        stats = cached_stats(page_size)
except Exception as exc:
    st.error(f"에듀넷 API에서 목록 정보를 불러오지 못했습니다: {exc}")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("2022 과목 수", f"{stats['total_items']:,}건")
m2.metric("선택한 API 페이지 크기 기준", f"{stats['api_pages']:,}페이지")
m3.metric("사이트 화면(15건씩) 환산", f"{stats['site_pages_at_15']:,}페이지")
st.caption("건수는 수집 시점에 따라 달라질 수 있습니다. 페이지 범위는 위에서 선택한 API 페이지 크기를 기준으로 합니다.")

if stats["api_pages"] < 1:
    st.warning("현재 조회된 2022 교육과정 자료가 없습니다.")
    st.stop()

col_start, col_end, col_workers = st.columns([1, 1, 1])
with col_start:
    start_page = st.number_input(
        "시작 페이지",
        min_value=1,
        max_value=stats["api_pages"],
        value=1,
        step=1,
        key=f"start_page_{page_size}",
    )
with col_end:
    end_page = st.number_input(
        "종료 페이지",
        min_value=1,
        max_value=stats["api_pages"],
        value=stats["api_pages"],
        step=1,
        key=f"end_page_{page_size}",
    )
with col_workers:
    workers = st.selectbox("동시 상세 조회 수", options=[1, 2, 3, 4], index=1)

interval_seconds = st.slider(
    "요청 시작 간격(초)",
    min_value=0.3,
    max_value=2.0,
    value=0.5,
    step=0.1,
    help="사이트에 부담을 주지 않도록 기본 간격을 적용합니다. 429 응답이 나오면 간격을 늘리세요.",
)

if start_page > end_page:
    st.error("시작 페이지는 종료 페이지보다 클 수 없습니다.")
    st.stop()

selected_pages = int(end_page - start_page + 1)
selected_records = min(
    stats["total_items"],
    max(0, stats["total_items"] - (int(start_page) - 1) * page_size),
    selected_pages * page_size,
)
estimated_minutes = selected_records * interval_seconds / 60
st.warning(
    f"선택 범위에는 최대 약 {selected_records:,}건이 포함됩니다. "
    f"교과서명 조회까지 요청 간격만으로 약 {estimated_minutes:.1f}분 이상 걸릴 수 있습니다. "
    "처음에는 1페이지로 시험한 뒤 전체 수집을 권장합니다."
)

if st.button("수집 시작", type="primary", disabled=(start_page > end_page)):
    progress = st.progress(0.0, text="수집 준비 중…")
    detail_status = st.empty()
    latest_progress = {"done": 0, "total": 0}

    def update_progress(done: int, total: int, subject: str) -> None:
        latest_progress["done"] = done
        latest_progress["total"] = total
        fraction = done / total if total else 0.0
        progress.progress(fraction, text=f"상세 조회 중: {done:,}/{total:,}건")
        if done == 1 or done % 25 == 0 or done == total:
            detail_status.caption(f"최근 처리 과목: {subject or '(과목명 없음)'}")

    try:
        with st.status("에듀넷 목록·상세를 수집하고 있습니다…", expanded=True) as status:
            status.write("2022 교육과정 목록을 확인합니다.")
            summary = crawl_2022_subjects(
                start_page=int(start_page),
                end_page=int(end_page),
                page_size=int(page_size),
                workers=int(workers),
                interval_seconds=float(interval_seconds),
                progress_callback=update_progress,
            )
            st.session_state["crawl_rows"] = summary.rows
            st.session_state["crawl_errors"] = summary.errors
            st.session_state["crawl_meta"] = {
                "total_items": summary.total_items,
                "total_site_pages": summary.total_site_pages,
                "api_pages_used": summary.api_pages_used,
                "first_page": summary.selected_first_page,
                "last_page": summary.selected_last_page,
            }
            status.update(label="수집이 완료되었습니다.", state="complete", expanded=False)
    except Exception as exc:
        st.error(f"수집 중 오류가 발생했습니다: {exc}")

if st.session_state.get("crawl_rows"):
    rows = st.session_state["crawl_rows"]
    errors = st.session_state.get("crawl_errors", [])
    meta = st.session_state.get("crawl_meta", {})
    frame = pd.DataFrame(rows)
    st.subheader(f"수집 결과: {len(frame):,}건")
    st.caption(
        f"선택 페이지 {meta.get('first_page', '?')}–{meta.get('last_page', '?')} · "
        f"API 목록 요청 {meta.get('api_pages_used', 0)}회 · "
        f"현재 전체 2022 자료 {meta.get('total_items', 0):,}건"
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_bytes = frame.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "CSV 다운로드 (Excel용 UTF-8 BOM)",
        data=csv_bytes,
        file_name=f"edunet_2022_approved_subjects_{stamp}.csv",
        mime="text/csv",
        type="primary",
    )
    if errors:
        st.warning(f"상세 조회 {len(errors):,}건이 실패했습니다. 해당 행은 목록 정보로 저장하고 교과서명은 빈칸으로 두었습니다.")
        with st.expander("상세 조회 실패 목록"):
            st.code("\n".join(errors[:200]))
            if len(errors) > 200:
                st.caption(f"처음 200건만 표시했습니다. 전체 실패 수: {len(errors):,}건")
else:
    st.caption("아직 수집 결과가 없습니다. 먼저 일부 페이지로 실행해 결과를 확인하세요.")

with st.expander("수집 방식과 주의 사항"):
    st.markdown(
        """
- 2022 교육과정 필터는 에듀넷 코드 `CRCLM01`을 사용합니다.
- 과목명·관련교과·학교급·기관·상태·작성일은 목록/상세 응답을 결합하고, 교과서명은 과목별 상세 조회에서 가져옵니다.
- 식별용 게시물 ID, 작성자 이름, 조회수 등 요청하지 않은 항목은 결과 CSV에 넣지 않습니다.
- 상세 조회 실패가 있으면 목록 자료는 남기되 교과서명은 비워 둡니다. 실패 목록을 확인하고 해당 페이지를 다시 실행할 수 있습니다.
- 전체 실행 전 일부 페이지로 먼저 동작을 확인하고, 데이터 이용 및 재배포 시 에듀넷의 이용약관·저작권 정책을 확인하세요.
        """
    )
