# upload_to_pg.py — 로컬에서 수집 후 PostgreSQL에 직접 저장
# 사용법: DATABASE_URL=postgresql://... python upload_to_pg.py

import os, time, re
import requests
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgres://", 1)
if not DATABASE_URL:
    print("❌ DATABASE_URL 환경변수가 없어요.")
    print("   실행 방법: DATABASE_URL='postgresql://...' python upload_to_pg.py")
    exit(1)

# ── getdb.py 원본 코드 그대로 ──────────────────────────

CINEMAS = [
    {"name":"KU시네마테크","source":"moviee","t_id":"121"},
    {"name":"KT&G상상마당시네마","source":"moviee","t_id":"123"},
    {"name":"서울아트시네마","source":"seoulart"},
    {"name":"한국영상자료원","source":"kofa"},
    {"name":"라이카시네마","source":"dtryx","brand":"spacedog","cinema_cd":"000072"},
    {"name":"씨네큐브","source":"dtryx","brand":"cinecube","cinema_cd":"000003"},
    {"name":"더숲아트시네마","source":"dtryx","brand":"indieart","cinema_cd":"000065"},
    {"name":"아트하우스모모","source":"dtryx","brand":"indieart","cinema_cd":"000067"},
    {"name":"서울영화센터","source":"dtryx","brand":"seoulcc","cinema_cd":"000160"},
    {"name":"아리랑시네센터","source":"dtryx","brand":"etc","cinema_cd":"000088"},
    {"name":"에무시네마","source":"dtryx","brand":"indieart","cinema_cd":"000069"},
    {"name":"아트나인","source":"dtryx","brand":"etc","cinema_cd":"000162"},
]

DTRYX_CGID = "FE8EF4D2-F22D-4802-A39A-D58F23A29C1E"

def make_datetime(date_obj, time_str):
    if not time_str: return None
    try:
        return datetime.combine(date_obj, datetime.strptime(time_str,"%H:%M").time())
    except:
        return None

def compute_end_dt(start_dt, runtime):
    return start_dt+timedelta(minutes=runtime) if start_dt and runtime else None

def compute_runtime(start_time, end_time):
    try:
        s = datetime.strptime(start_time, "%H:%M")
        e = datetime.strptime(end_time, "%H:%M")
        return int((e-s).total_seconds()//60)
    except:
        return None

def fetch_dtryx(cinema, start_date, days=14):
    rows=[]
    for i in range(days):
        day=start_date+timedelta(days=i)
        day_str=day.strftime("%Y-%m-%d")
        params={"cgid":DTRYX_CGID,"BrandCd":cinema["brand"],"CinemaCd":cinema["cinema_cd"],
                "PlaySDT":day_str,"_":int(time.time()*1000)}
        headers={"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest"}
        try:
            data=requests.get("https://www.dtryx.com/cinema/showseq_list.do", params=params, headers=headers, timeout=10).json()
        except:
            continue
        for item in data.get("Showseqlist",[]):
            start=item.get("StartTime"); end=item.get("EndTime")
            runtime=compute_runtime(start,end)
            start_dt=make_datetime(day,start)
            end_dt=make_datetime(day,end) if end else compute_end_dt(start_dt,runtime)
            special_fields=[item.get(f,"").strip() for f in ["ScreenTypeNmNat","PlayTimeTypeNm","DisplayTypeDetailNm","ScreeningInfoNat"]]
            show_type="일반" if all(f in ["","일반"] for f in special_fields) else " / ".join(f for f in special_fields if f not in ["","일반"])
            screen_name=item.get("ScreenNmNat") or item.get("ScreenNm") or ""
            program=item.get("ProgramName","").strip() if item.get("ProgramName") else ""
            rows.append({"cinema":cinema["name"],"movie":item.get("MovieNmNat"),
                         "start_dt":start_dt,"end_dt":end_dt,"runtime":runtime,
                         "screen":screen_name,"source":"dtryx",
                         "show_type":show_type,"program":program,
                         "movie_url":"","movie_cd":item.get("MovieCd","").strip()})
    return rows

def fetch_moviee(cinema, start_date, days=14):
    rows=[]
    url="https://moviee.co.kr/api/TicketApi/GetPlayTimeList"
    for i in range(days):
        day=start_date+timedelta(days=i)
        params={"tId":cinema["t_id"],"playDt":day.strftime("%Y-%m-%d")}
        try:
            data=requests.get(url, params=params, timeout=10).json()
        except:
            continue
        if data.get("ResCd")!="00": continue
        for item in data["ResData"]["Table"]:
            start=item["PLAY_TIME"]; end=item["END_TIME"]
            start=f"{start[:2]}:{start[2:]}" if start else None
            end=f"{end[:2]}:{end[2:]}" if end else None
            runtime=compute_runtime(start,end)
            start_dt=make_datetime(day,start)
            end_dt=make_datetime(day,end) if end else compute_end_dt(start_dt,runtime)
            movie_name=item["M_NM"]
            show_type=""
            if "(" in movie_name and ")" in movie_name:
                main_title=movie_name.split("(")[0].strip()
                show_type=movie_name.split("(")[1].replace(")","").strip()
            else:
                main_title=movie_name.strip()
            rows.append({"cinema":cinema["name"],"movie":main_title,
                         "start_dt":start_dt,"end_dt":end_dt,"runtime":runtime,
                         "screen":item.get("ROOM_NM",""),"source":"moviee",
                         "show_type":show_type,"program":""})
    return rows

def fetch_seoulart(cinema):
    rows=[]
    url="https://www.cinematheque.seoul.kr/bbs/content.php?co_id=timetable"
    try:
        res=requests.get(url, timeout=10)
        soup=BeautifulSoup(res.text,"html.parser")
    except:
        return rows
    year=datetime.today().year
    theater_name=cinema["name"]
    for table in soup.select("table"):
        for dr in table.select("tr.date-label"):
            col_dates=[]
            for td in dr.find_all("td"):
                txt=td.get_text(strip=True)
                if txt:
                    m,d,_=txt.split(".")
                    col_dates.append(datetime(year,int(m),int(d)))
                else:
                    col_dates.append(None)
            next_tr=dr.find_next_sibling()
            while next_tr and "event" in next_tr.get("class",[]):
                for idx, td in enumerate(next_tr.find_all("td")):
                    date_obj=col_dates[idx] if idx<len(col_dates) else None
                    if not date_obj: continue
                    for link in td.find_all("a"):
                        try:
                            start=link.find("strong").text.strip()
                            title_tag=link.find_all("p")[1]
                            title=re.sub(r"\(\d+min\)","",title_tag.text).strip()
                            runtime_match=re.search(r"\((\d+)min\)",title_tag.text)
                            runtime=int(runtime_match.group(1)) if runtime_match else None
                            start_dt=make_datetime(date_obj,start)
                            end_dt=compute_end_dt(start_dt,runtime)
                            rows.append({"cinema":theater_name,"movie":title,
                                         "start_dt":start_dt,"end_dt":end_dt,"runtime":runtime,
                                         "screen":"","source":"seoulart","show_type":"","program":""})
                        except:
                            continue
                next_tr=next_tr.find_next_sibling()
    return rows

def fetch_kofa(cinema):
    rows=[]
    url="https://www.koreafilm.or.kr/cinematheque/schedule"
    try:
        res=requests.get(url, timeout=10)
        soup=BeautifulSoup(res.text,"html.parser")
    except:
        return rows
    today=datetime.today()
    month=today.month; prev_day=None; year=today.year
    for block in soup.find_all("dl","list-day-1"):
        date_tag=block.find("dt","txt-day")
        if not date_tag: continue
        txt=date_tag.text.strip()
        if "." not in txt: continue
        day=int(txt.split(".")[0])
        if prev_day and day<prev_day: month+=1
        prev_day=day
        date_obj=datetime(year,month,day)
        for s in block.select("ul.list-detail-1"):
            start_tag=s.select_one(".txt-time")
            start=start_tag.get_text(strip=True) if start_tag else None
            runtime_tag=s.select_one(".min")
            if runtime_tag:
                for strong in runtime_tag.find_all("strong"): strong.decompose()
                runtime_text=runtime_tag.get_text(strip=True).replace("분","")
                runtime=int(runtime_text) if runtime_text.isdigit() else None
            else:
                runtime=None
            end_dt=compute_end_dt(make_datetime(date_obj,start),runtime)
            title_tag=s.select_one(".txt-1 a")
            screen_tag=s.select_one(".txt-room")
            type_tag=s.select_one(".fomat")
            program_tag=s.select_one(".layer-txt-1")
            # kofa 영화 상세 링크 추출
            movie_href = title_tag.get("href","") if title_tag else ""
            movie_url = ("https://www.koreafilm.or.kr" + movie_href) if movie_href else ""
            rows.append({
                "cinema":cinema["name"],
                "movie":title_tag.get_text(strip=True) if title_tag else "",
                "start_dt":make_datetime(date_obj,start),
                "end_dt":end_dt, "runtime":runtime,
                "screen":screen_tag.get_text(strip=True) if screen_tag else "",
                "source":"kofa",
                "show_type":type_tag.get_text(strip=True)[4:] if type_tag else "",
                "program":program_tag.get_text(strip=True) if program_tag else "",
                "movie_url": movie_url
            })
    return rows

# ── 포스터 수집 (dtryx 상세 페이지) ─────────────────
def fetch_poster_from_dtryx(movie_cd):
    """dtryx 영화 상세 페이지에서 포스터 URL, 줄거리, 감독 추출"""
    if not movie_cd:
        return {}
    url = f"https://www.dtryx.com/movie/view.do?cgid={DTRYX_CGID}&MovieCd={movie_cd}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        # 포스터: img.dtryx.com URL을 가진 첫 번째 img
        poster_url = ""
        for img in soup.find_all("img"):
            src = img.get("src","")
            if "img.dtryx.com" in src and ".small." in src:
                poster_url = src
                break
        # small 없으면 img.dtryx.com 아무거나
        if not poster_url:
            for img in soup.find_all("img"):
                src = img.get("src","")
                if "img.dtryx.com" in src:
                    poster_url = src
                    break
        # 최후 fallback: og:image
        if not poster_url:
            og = soup.find("meta", property="og:image")
            poster_url = og["content"] if og else ""
        # 감독
        director = ""
        for dt in soup.select("dl dt, .movie-info dt"):
            if "감독" in dt.get_text():
                dd = dt.find_next_sibling("dd")
                if dd:
                    director = dd.get_text(strip=True)
                    break
        # 줄거리
        synopsis = ""
        for sel in [".movie-synopsis", ".synopsis", ".txt-synopsis", ".movie-info-txt"]:
            el = soup.select_one(sel)
            if el:
                synopsis = el.get_text(strip=True)[:500]
                break
        return {"poster_url": poster_url, "director": director, "synopsis": synopsis}
    except Exception as e:
        print(f"  포스터 수집 실패 ({movie_cd}): {e}")
        return {}

def collect_posters(all_rows):
    """all_rows에서 movie_cd 수집 → dtryx 상세 크롤링 → {title: {poster_url, director, synopsis}}"""
    # movie_cd가 있는 것만, 제목별로 대표 movie_cd 추출
    cd_map = {}  # movie_title → movie_cd
    for r in all_rows:
        cd = r.get("movie_cd","")
        if cd and r["movie"] and r["movie"] not in cd_map:
            cd_map[r["movie"]] = cd

    result = {}
    print(f"\n🎬 포스터 수집 시작: {len(cd_map)}편")
    for title, cd in cd_map.items():
        info = fetch_poster_from_dtryx(cd)
        if info.get("poster_url"):
            result[title] = info
            print(f"  ✅ {title}: {info['poster_url'][:60]}…")
        else:
            print(f"  ⚠️  {title}: 포스터 없음")
        time.sleep(0.3)  # 서버 부하 방지
    return result

def save_movies_to_pg(poster_data, conn):
    """movies 테이블에 포스터 URL, 감독, 줄거리 upsert"""
    if not poster_data:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id SERIAL PRIMARY KEY,
            title TEXT UNIQUE,
            director TEXT,
            synopsis TEXT,
            poster_url TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for title, info in poster_data.items():
        cur.execute("""
            INSERT INTO movies (title, director, synopsis, poster_url, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (title) DO UPDATE SET
                director=EXCLUDED.director,
                synopsis=EXCLUDED.synopsis,
                poster_url=EXCLUDED.poster_url,
                updated_at=NOW()
        """, (title, info.get("director",""), info.get("synopsis",""), info.get("poster_url","")))
    conn.commit()
    cur.close()
    print(f"✅ movies 테이블 저장 완료: {len(poster_data)}편")

# ── PostgreSQL 저장 ───────────────────────────────────
def save_to_pg(rows):
    if not rows: return
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screenings (
            cinema TEXT, movie TEXT, start_dt TEXT, end_dt TEXT,
            runtime INTEGER, screen TEXT, source TEXT,
            show_type TEXT, program TEXT, movie_url TEXT,
            PRIMARY KEY(cinema, start_dt, screen))
    """)
    # 기존 테이블에 컬럼 없으면 추가
    cur.execute("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS movie_url TEXT")
    cur.execute("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS movie_cd TEXT")
    cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    for r in rows:
        cur.execute("""
            INSERT INTO screenings (cinema,movie,start_dt,end_dt,runtime,screen,source,show_type,program,movie_url,movie_cd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cinema, start_dt, screen) DO UPDATE SET
                movie=EXCLUDED.movie, end_dt=EXCLUDED.end_dt,
                runtime=EXCLUDED.runtime, source=EXCLUDED.source,
                show_type=EXCLUDED.show_type, program=EXCLUDED.program,
                movie_url=EXCLUDED.movie_url, movie_cd=EXCLUDED.movie_cd
        """, (
            r["cinema"], r["movie"],
            str(r["start_dt"]) if r["start_dt"] else None,
            str(r["end_dt"])   if r["end_dt"]   else None,
            r["runtime"], r["screen"], r["source"],
            r.get("show_type",""), r.get("program",""),
            r.get("movie_url",""), r.get("movie_cd","")
        ))
    cur.execute("""
        INSERT INTO meta (key,value) VALUES ('last_updated',%s)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
    """, (datetime.now().isoformat(),))
    conn.commit()
    # 포스터 수집 & 저장
    poster_data = collect_posters(rows)
    save_movies_to_pg(poster_data, psycopg2.connect(DATABASE_URL))
    cur.close(); conn.close()

# ── 실행 ─────────────────────────────────────────────
if __name__ == "__main__":
    start_time = time.time()
    all_rows = []
    today = datetime.today()
    for cinema in CINEMAS:
        src = cinema["source"]
        try:
            if src=="dtryx":      rows=fetch_dtryx(cinema,today,days=14)
            elif src=="moviee":   rows=fetch_moviee(cinema,today,days=14)
            elif src=="seoulart": rows=fetch_seoulart(cinema)
            elif src=="kofa":     rows=fetch_kofa(cinema)
            else: rows=[]
            all_rows += rows
            print(f"✅ {cinema['name']}: {len(rows)}건")
        except Exception as e:
            print(f"❌ {cinema['name']} 오류: {e}")

    all_rows = sorted(all_rows, key=lambda x: (x['start_dt'] or datetime.max, x['end_dt'] or datetime.max))
    print(f"\n총 {len(all_rows)}건 수집 → PostgreSQL 저장 중...")
    save_to_pg(all_rows)

    # 영화 제목 글자수 통계
    titles = list({r['movie'] for r in all_rows if r.get('movie')})
    if titles:
        lens = [len(t) for t in titles]
        max_len = max(lens)
        avg_len = sum(lens) / len(lens)
        longest = max(titles, key=len)
        print(f"\n📊 영화 제목 통계: {len(titles)}편")
        print(f"   최대 {max_len}자: 『{longest}』")
        print(f"   평균 {avg_len:.1f}자")

    print(f"✅ 완료! 처리시간: {time.time()-start_time:.1f}초")
