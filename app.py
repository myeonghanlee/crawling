import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By  # 👈 이 줄을 반드시 추가하세요.
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
import time
from io import BytesIO

st.set_page_config(page_title="에듀넷 과목 크롤러", layout="wide")

st.title("📚 에듀넷 2022 교육과정 과목 크롤러")
st.markdown("버튼을 누르면 실시간으로 데이터를 수집하여 표로 보여줍니다.")

def run_crawler():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    
    # Streamlit Cloud 등 리눅스 환경 호환을 위한 드라이버 설정
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    except Exception:
        # 시스템에 설치된 chromium-driver 경로 직접 참조 (Streamlit Cloud 대체 방식)
        driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)

    url = "https://www.edunet.net/sprApprSbjct/list/11"
    driver.get(url)
    
    crawled_data = []
    page_num = 1
    
    # UI 피드백을 위한 Streamlit 요소
    status_text = st.empty()
    progress_bar = st.progress(0)

    # 예시로 3페이지만 수집하도록 제한 (Streamlit 서버 타임아웃 방지)
    max_pages = 3 
    
    while page_num <= max_pages:
        status_text.text(f"수집 중: {page_num}페이지 데이터를 읽어오고 있습니다...")
        time.sleep(3) 

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        rows = soup.select('table tbody tr')

        for row in rows:
            columns = row.find_all('td')
            if len(columns) >= 7:
                data = {
                    "과목명": columns[0].text.strip(),
                    "관련교과(교과군)": columns[1].text.strip(),
                    "학교급": columns[2].text.strip(),
                    "소속(기관)": columns[3].text.strip(),
                    "교과서명": columns[4].text.strip(),
                    "상태": columns[5].text.strip(),
                    "작성일": columns[6].text.strip()
                }
                if "2022" in data["관련교과(교과군)"]:
                    crawled_data.append(data)
        
        # Streamlit 프로그레스 바 업데이트
        progress_bar.progress(int((page_num / max_pages) * 100))
        page_num += 1
        
        if page_num > max_pages:
            break
            
        try:
            # <a> 태그의 텍스트가 해당 페이지 번호와 일치하는 요소를 찾음
            next_page_btn = driver.find_element(By.XPATH, f"//a[normalize-space()='{page_num}']")
            
            # .click() 사용 시 화면 가림막(팝업 등)에 막히는 것을 방지하기 위해 자바스크립트로 요소 직접 클릭
            driver.execute_script("arguments[0].click();", next_page_btn)
            
        except Exception as e:
            # 마지막 페이지이거나 다음 페이지 버튼을 찾을 수 없는 경우 무한 루프 방지
            st.info(f"더 이상 이동할 페이지가 없습니다. (현재 수집 완료: {page_num-1}페이지)")
            break
        
    driver.quit()
    status_text.success("데이터 수집이 완료되었습니다!")
    return pd.DataFrame(crawled_data)

# 크롤링 실행 버튼
if st.button("🚀 크롤링 시작"):
    with st.spinner("백그라운드에서 브라우저를 구동 중입니다..."):
        df = run_crawler()
    
    if not df.empty:
        st.subheader("수집된 데이터 미리보기")
        st.dataframe(df)
        
        # 데이터프레임을 엑셀 파일(메모리 버퍼)로 변환
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')
        excel_data = output.getvalue()
        
        # 엑셀 다운로드 버튼
        st.download_button(
            label="📥 엑셀(.xlsx) 파일 다운로드",
            data=excel_data,
            file_name="edunet_2022_subjects.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("조건에 맞는 데이터가 수집되지 않았습니다.")
