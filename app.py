import io
import time
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="에듀넷 로그인 크롤러", layout="wide")
st.title("🔐 에듀넷 승인 과목 수집기 (로그인 세션 기반)")
st.caption("로그인된 세션 쿠키를 이용해 2022 개정 승인 과목을 안전하게 추출합니다.")

# 사이드바 설정
with st.sidebar:
    st.header("1. 인증 정보 입력")
    # 브라우저에서 복사한 Cookie 전체 문자열 입력
    cookie_input = st.text_area(
        "브라우저의 Cookie 값을 붙여넣으세요:",
        placeholder="예: JSESSIONID=AbCdEf1234...; WMONID=xyz...",
        help="에듀넷 로그인 후 F12 -> Network 탭 -> Request Headers의 Cookie 전체를 복사해 붙여넣습니다.",
        height=120,
    )

    st.header("2. 수집 조건")
    regions = {
        "서울 (11)": "11",
        "경기 (41)": "41",
        "부산 (26)": "26",
        "대구 (27)": "27",
        "인천 (28)": "28",
        "세종 (36)": "36",
    }
    selected_region = st.selectbox("지역 선택", list(regions.keys()))
    region_code = regions[selected_region]

    max_pages = st.number_input(
        "수집할 최대 페이지 수", min_value=1, max_value=50, value=5
    )
    filter_2022 = st.checkbox("2022 교육과정만 필터링", value=True)

    start_btn = st.button("🚀 크롤링 시작", type="primary")


# 크롤링 함수
def crawl_authenticated(cookie_str, region, max_p, only_2022):
    session = requests.Session()

    # 필수 헤더 설정 (Cookie 및 User-Agent)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://www.edunet.net/sprApprSbjct/list/{region}",
        "Cookie": cookie_str.strip(),
    }
    session.headers.update(headers)

    base_url = "https://www.edunet.net"
    data = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for page in range(1, max_p + 1):
        status_text.text(f"[{page}/{max_p} 페이지] 접근 중...")
        url = f"{base_url}/sprApprSbjct/list/{region}?pageIndex={page}"

        try:
            res = session.get(url, timeout=10)

            # 로그인 만료 체크 (로그인 페이지로 튕겼는지 확인)
            if "login" in res.url.lower() or "로그인" in res.text[:500]:
                st.error(
                    "❌ 로그인 세션이 만료되었거나 쿠키가 올바르지 않습니다. 다시 로그인 후 쿠키를 복사해주세요."
                )
                break

            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table tbody tr")

            if not rows or "조회된 내역이 없습니다" in rows[0].get_text():
                status_text.text("수집 완료: 마지막 페이지에 도달했습니다.")
                break

            for tr in rows:
                tds = tr.select("td")
                if len(tds) < 4:
                    continue

                row_str = " ".join([td.get_text(strip=True) for td in tds])

                # 2022 교육과정 필터링
                if only_2022 and "2022" not in row_str:
                    continue

                # 테이블 파싱
                title_tag = tr.select_one("a")
                subject_name = (
                    title_tag.get_text(strip=True)
                    if title_tag
                    else (tds[2].get_text(strip=True) if len(tds) > 2 else "")
                )

                item = {
                    "과목명": subject_name,
                    "관련교과(교과군)": (
                        tds[2].get_text(strip=True) if len(tds) > 2 else ""
                    ),
                    "학교급": (
                        tds[1].get_text(strip=True) if len(tds) > 1 else ""
                    ),
                    "소속(기관)": (
                        tds[5].get_text(strip=True) if len(tds) > 5 else ""
                    ),
                    "교과서명": (
                        tds[4].get_text(strip=True) if len(tds) > 4 else ""
                    ),
                    "상태": (
                        tds[6].get_text(strip=True) if len(tds) > 6 else ""
                    ),
                    "작성일": (
                        tds[7].get_text(strip=True) if len(tds) > 7 else ""
                    ),
                }
                data.append(item)

            progress_bar.progress(page / max_p)
            time.sleep(1)

        except Exception as e:
            st.error(f"오류 발생: {e}")
            break

    status_text.text("모든 페이지 수집 작업이 종료되었습니다.")
    return pd.DataFrame(data)


# 실행 영역
if start_btn:
    if not cookie_input:
        st.warning("⚠️ 사이드바에 에듀넷 Cookie 값을 먼저 입력해 주세요.")
    else:
        with st.spinner("로그인 세션으로 수집하는 중..."):
            df = crawl_authenticated(
                cookie_input, region_code, max_pages, filter_2022
            )

        if not df.empty:
            df.drop_duplicates(inplace=True)
            st.success(f"🎉 총 {len(df)}건의 승인 과목을 수집했습니다!")

            # 미리보기 표
            st.dataframe(df, use_container_width=True)

            # 엑셀 다운로드 버퍼 생성
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="2022_승인과목")
            excel_data = excel_buffer.getvalue()

            st.download_button(
                label="📥 엑셀(.xlsx) 파일 다운로드",
                data=excel_data,
                file_name=f"2022_교육감승인과목_{selected_region.split()[0]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("조건에 일치하는 데이터가 없습니다.")
