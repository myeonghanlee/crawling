import io
import time
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st

# 페이지 제목 및 설정
st.set_page_config(page_title="에듀넷 승인과목 크롤러", layout="wide")
st.title("📚 에듀넷 교육감 승인 과목 수집기")
st.markdown("2022 개정 교육과정 승인 과목 데이터를 수집하여 엑셀로 내려받습니다.")

# 1. 사이드바 설정 (사용자 입력)
with st.sidebar:
    st.header("⚙️ 검색 설정")
    regions = {
        "서울 (11)": "11",
        "경기 (41)": "41",
        "부산 (26)": "26",
        "대구 (27)": "27",
        "인천 (28)": "28",
        "광주 (29)": "29",
        "대전 (30)": "30",
        "세종 (36)": "36",
    }
    selected_region = st.selectbox("지역(교육청) 선택", list(regions.keys()))
    region_code = regions[selected_region]

    max_pages = st.number_input("수집할 최대 페이지 수", min_value=1, max_value=30, value=5)
    filter_2022 = st.checkbox("2022 교육과정만 필터링", value=True)
    start_btn = st.button("🚀 크롤링 시작", type="primary")


# 2. 크롤링 함수 정의
def crawl_edunet(region, max_p, only_2022):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    base_url = "https://www.edunet.net"
    data = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for page in range(1, max_p + 1):
        status_text.text(f"[{page}/{max_p} 페이지] 데이터 수집 중...")
        url = f"{base_url}/sprApprSbjct/list/{region}?pageIndex={page}"

        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code != 200:
                st.warning(f"{page}페이지 응답 오류 (코드: {res.status_code})")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table tbody tr")

            if not rows or "조회된 내역이 없습니다" in rows[0].get_text():
                status_text.text("더 이상 데이터가 없어 수집을 마칩니다.")
                break

            for tr in rows:
                tds = tr.select("td")
                if len(tds) < 4:
                    continue

                row_str = " ".join([td.get_text(strip=True) for td in tds])

                # 2022 교육과정 필터링
                if only_2022 and "2022" not in row_str:
                    continue

                # 테이블 열 파싱 (과목명 링크 추출 포함)
                title_tag = tr.select_one("a")
                subject_name = title_tag.get_text(strip=True) if title_tag else tds[2].get_text(strip=True)

                # 사이트 컬럼 구조 매핑
                item = {
                    "과목명": subject_name,
                    "관련교과(교과군)": tds[2].get_text(strip=True) if len(tds) > 2 else "",
                    "학교급": tds[1].get_text(strip=True) if len(tds) > 1 else "",
                    "소속(기관)": tds[5].get_text(strip=True) if len(tds) > 5 else "",
                    "교과서명": tds[4].get_text(strip=True) if len(tds) > 4 else "",
                    "상태": tds[6].get_text(strip=True) if len(tds) > 6 else "",
                    "작성일": tds[7].get_text(strip=True) if len(tds) > 7 else "",
                }
                data.append(item)

            progress_bar.progress(page / max_p)
            time.sleep(1)  # 서버 부하 방지용 대기

        except Exception as e:
            st.error(f"오류 발생: {e}")
            break

    status_text.text("수집 완료!")
    return pd.DataFrame(data)


# 3. 크롤링 실행 및 결과 표시
if start_btn:
    with st.spinner("데이터를 수집하고 있습니다. 잠시만 기다려주세요..."):
        df_result = crawl_edunet(region_code, max_pages, filter_2022)

    if not df_result.empty:
        # 중복 제거
        df_result.drop_duplicates(inplace=True)
        st.success(f"총 {len(df_result)}건의 과목 데이터를 수집했습니다!")

        # 표로 미리보기
        st.dataframe(df_result, use_container_width=True)

        # 엑셀 다운로드 파일 생성 (인메모리 버퍼)
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df_result.to_excel(writer, index=False, sheet_name="2022_승인과목")
        excel_data = excel_buffer.getvalue()

        # 다운로드 버튼 표시
        st.download_button(
            label="📥 엑셀 파일(.xlsx) 다운로드",
            data=excel_data,
            file_name=f"교육감승인과목_{selected_region.split()[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.warning("조건에 해당하는 데이터를 찾지 못했습니다.")
