# app.py — 서울 독립극장 시간표 서비스 (클라우드 배포용)
import sqlite3, json, io, os, threading, logging
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
DB_PATH = os.environ.get("DB_PATH", "screenings.db")

CINEMA_ORDER = [
    "KU시네마테크","KT&G상상마당시네마","서울아트시네마","한국영상자료원",
    "라이카시네마","씨네큐브","더숲아트시네마","아트하우스모모",
    "서울영화센터","아리랑시네센터","에무시네마","아트나인"
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS screenings (
        cinema TEXT, movie TEXT, start_dt TEXT, end_dt TEXT,
        runtime INTEGER, screen TEXT, source TEXT,
        show_type TEXT, program TEXT,
        PRIMARY KEY(cinema, start_dt, screen))""")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit(); conn.close()

import requests as req_lib
from bs4 import BeautifulSoup
import time, re

DTRYX_CGID = "FE8EF4D2-F22D-4802-A39A-D58F23A29C1E"
CINEMAS = [
    {"name":"KU시네마테크",       "source":"moviee",  "t_id":"121"},
    {"name":"KT&G상상마당시네마", "source":"moviee",  "t_id":"123"},
    {"name":"서울아트시네마",     "source":"seoulart"},
    {"name":"한국영상자료원",     "source":"kofa"},
    {"name":"라이카시네마",       "source":"dtryx","brand":"spacedog", "cinema_cd":"000072"},
    {"name":"씨네큐브",          "source":"dtryx","brand":"cinecube", "cinema_cd":"000003"},
    {"name":"더숲아트시네마",     "source":"dtryx","brand":"indieart", "cinema_cd":"000065"},
    {"name":"아트하우스모모",     "source":"dtryx","brand":"indieart", "cinema_cd":"000067"},
    {"name":"서울영화센터",       "source":"dtryx","brand":"seoulcc",  "cinema_cd":"000160"},
    {"name":"아리랑시네센터",     "source":"dtryx","brand":"etc",      "cinema_cd":"000088"},
    {"name":"에무시네마",         "source":"dtryx","brand":"indieart", "cinema_cd":"000069"},
    {"name":"아트나인",           "source":"dtryx","brand":"etc",      "cinema_cd":"000162"},
]

def make_dt(date_obj, time_str):
    if not time_str: return None
    try: return datetime.combine(date_obj, datetime.strptime(time_str,"%H:%M").time())
    except: return None

def end_dt(s, r): return s+timedelta(minutes=r) if s and r else None

def calc_rt(s, e):
    try:
        return int((datetime.strptime(e,"%H:%M")-datetime.strptime(s,"%H:%M")).total_seconds()//60)
    except: return None

def fetch_dtryx(cinema, today, days=14):
    rows=[]
    for i in range(days):
        day=today+timedelta(days=i); ds=day.strftime("%Y-%m-%d")
        try:
            data=req_lib.get("https://www.dtryx.com/cinema/showseq_list.do",
                params={"cgid":DTRYX_CGID,"BrandCd":cinema["brand"],"CinemaCd":cinema["cinema_cd"],
                        "PlaySDT":ds,"_":int(time.time()*1000)},
                headers={"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest"},timeout=10).json()
        except: continue
        for item in data.get("Showseqlist",[]):
            s=item.get("StartTime"); e=item.get("EndTime"); rt=calc_rt(s,e)
            sdt=make_dt(day,s); edt=make_dt(day,e) or end_dt(sdt,rt)
            sf=[item.get(f,"").strip() for f in ["ScreenTypeNmNat","PlayTimeTypeNm","DisplayTypeDetailNm","ScreeningInfoNat"]]
            st="일반" if all(f in ["","일반"] for f in sf) else " / ".join(f for f in sf if f not in ["","일반"])
            rows.append({"cinema":cinema["name"],"movie":item.get("MovieNmNat"),
                "start_dt":sdt,"end_dt":edt,"runtime":rt,
                "screen":item.get("ScreenNmNat") or item.get("ScreenNm") or "",
                "source":"dtryx","show_type":st,"program":item.get("ProgramName","").strip()})
    return rows

def fetch_moviee(cinema, today, days=14):
    rows=[]
    for i in range(days):
        day=today+timedelta(days=i)
        try:
            data=req_lib.get("https://moviee.co.kr/api/TicketApi/GetPlayTimeList",
                params={"tId":cinema["t_id"],"playDt":day.strftime("%Y-%m-%d")},timeout=10).json()
        except: continue
        if data.get("ResCd")!="00": continue
        for item in data["ResData"]["Table"]:
            s=item["PLAY_TIME"]; e=item["END_TIME"]
            s=f"{s[:2]}:{s[2:]}" if s else None; e=f"{e[:2]}:{e[2:]}" if e else None
            rt=calc_rt(s,e); sdt=make_dt(day,s); edt=make_dt(day,e) or end_dt(sdt,rt)
            mn=item["M_NM"]
            if "(" in mn and ")" in mn: title=mn.split("(")[0].strip(); st=mn.split("(")[1].replace(")","").strip()
            else: title=mn.strip(); st=""
            rows.append({"cinema":cinema["name"],"movie":title,"start_dt":sdt,"end_dt":edt,
                "runtime":rt,"screen":item.get("ROOM_NM",""),"source":"moviee","show_type":st,"program":""})
    return rows

def fetch_seoulart(cinema):
    rows=[]
    try:
        soup=BeautifulSoup(req_lib.get("https://www.cinematheque.seoul.kr/bbs/content.php?co_id=timetable",timeout=10).text,"html.parser")
    except: return rows
    year=datetime.today().year
    for table in soup.select("table"):
        for dr in table.select("tr.date-label"):
            col_dates=[]
            for td in dr.find_all("td"):
                txt=td.get_text(strip=True)
                if txt:
                    m,d,_=txt.split("."); col_dates.append(datetime(year,int(m),int(d)))
                else: col_dates.append(None)
            ntr=dr.find_next_sibling()
            while ntr and "event" in ntr.get("class",[]):
                for idx,td in enumerate(ntr.find_all("td")):
                    dobj=col_dates[idx] if idx<len(col_dates) else None
                    if not dobj: continue
                    for link in td.find_all("a"):
                        try:
                            s=link.find("strong").text.strip()
                            tp=link.find_all("p")[1]
                            title=re.sub(r"\(\d+min\)","",tp.text).strip()
                            rm=re.search(r"\((\d+)min\)",tp.text)
                            rt=int(rm.group(1)) if rm else None
                            sdt=make_dt(dobj,s)
                            rows.append({"cinema":cinema["name"],"movie":title,"start_dt":sdt,
                                "end_dt":end_dt(sdt,rt),"runtime":rt,"screen":"",
                                "source":"seoulart","show_type":"","program":""})
                        except: continue
                ntr=ntr.find_next_sibling()
    return rows

def fetch_kofa(cinema):
    rows=[]
    try:
        soup=BeautifulSoup(req_lib.get("https://www.koreafilm.or.kr/cinematheque/schedule",timeout=10).text,"html.parser")
    except: return rows
    today=datetime.today(); month=today.month; prev_day=None; year=today.year
    for block in soup.find_all("dl","list-day-1"):
        dt_tag=block.find("dt","txt-day")
        if not dt_tag or "." not in dt_tag.text: continue
        day=int(dt_tag.text.strip().split(".")[0])
        if prev_day and day<prev_day: month+=1
        prev_day=day; dobj=datetime(year,month,day)
        for s in block.select("ul.list-detail-1"):
            st_tag=s.select_one(".txt-time"); start=st_tag.get_text(strip=True) if st_tag else None
            rt_tag=s.select_one(".min")
            if rt_tag:
                for strong in rt_tag.find_all("strong"): strong.decompose()
                rt_txt=rt_tag.get_text(strip=True).replace("분","")
                rt=int(rt_txt) if rt_txt.isdigit() else None
            else: rt=None
            sdt=make_dt(dobj,start)
            tl=s.select_one(".txt-1 a"); sc=s.select_one(".txt-room")
            ty=s.select_one(".fomat"); pg=s.select_one(".layer-txt-1")
            rows.append({"cinema":cinema["name"],"movie":tl.get_text(strip=True) if tl else "",
                "start_dt":sdt,"end_dt":end_dt(sdt,rt),"runtime":rt,
                "screen":sc.get_text(strip=True) if sc else "",
                "source":"kofa","show_type":ty.get_text(strip=True)[4:] if ty else "",
                "program":pg.get_text(strip=True) if pg else ""})
    return rows

def run_crawl():
    log.info("크롤링 시작")
    t0=time.time(); all_rows=[]; today=datetime.today()
    for cinema in CINEMAS:
        src=cinema["source"]
        try:
            if src=="dtryx": rows=fetch_dtryx(cinema,today,14)
            elif src=="moviee": rows=fetch_moviee(cinema,today,14)
            elif src=="seoulart": rows=fetch_seoulart(cinema)
            elif src=="kofa": rows=fetch_kofa(cinema)
            else: rows=[]
            all_rows+=rows; log.info(f"  {cinema['name']}: {len(rows)}건")
        except Exception as e: log.error(f"  {cinema['name']} 오류: {e}")
    all_rows.sort(key=lambda x:(x["start_dt"] or datetime.max, x["end_dt"] or datetime.max))
    conn=sqlite3.connect(DB_PATH)
    for r in all_rows:
        conn.execute("INSERT OR REPLACE INTO screenings VALUES(?,?,?,?,?,?,?,?,?)",
            (r["cinema"],r["movie"],
             str(r["start_dt"]) if r["start_dt"] else None,
             str(r["end_dt"]) if r["end_dt"] else None,
             r["runtime"],r["screen"],r["source"],r.get("show_type",""),r.get("program","")))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('last_updated',?)",(datetime.now().isoformat(),))
    conn.commit(); conn.close()
    log.info(f"완료: {len(all_rows)}건 / {time.time()-t0:.1f}초")
    return len(all_rows)

def start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched=BackgroundScheduler(timezone="Asia/Seoul")
        sched.add_job(run_crawl,"cron",hour=6,minute=0)
        sched.start(); log.info("스케줄러: 매일 06:00 자동 갱신")
    except Exception as e: log.warning(f"스케줄러 비활성화: {e}")

@app.route("/")
def index():
    with open(os.path.join(app.template_folder,"index.html"),encoding="utf-8") as f:
        return f.read()

@app.route("/api/screenings")
def api_screenings():
    df=request.args.get("date_from",date.today().isoformat())
    dt=request.args.get("date_to",  date.today().isoformat())
    cinemas=request.args.getlist("cinema")
    mq=request.args.get("movie","").strip()
    conn=get_db()
    sql="SELECT * FROM screenings WHERE date(start_dt) BETWEEN ? AND ?"
    params=[df,dt]
    if cinemas: sql+=f" AND cinema IN ({','.join('?'*len(cinemas))})"; params+=cinemas
    if mq: sql+=" AND movie LIKE ?"; params.append(f"%{mq}%")
    sql+=" ORDER BY start_dt ASC"
    rows=conn.execute(sql,params).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/cinemas")
def api_cinemas():
    conn=get_db()
    names=[r["cinema"] for r in conn.execute("SELECT DISTINCT cinema FROM screenings").fetchall()]
    conn.close()
    ordered=[c for c in CINEMA_ORDER if c in names]+[c for c in names if c not in CINEMA_ORDER]
    return jsonify(ordered)

@app.route("/api/stats")
def api_stats():
    conn=get_db()
    total=conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0]
    cc=conn.execute("SELECT COUNT(DISTINCT cinema) FROM screenings").fetchone()[0]
    mc=conn.execute("SELECT COUNT(DISTINCT movie) FROM screenings").fetchone()[0]
    lu=conn.execute("SELECT value FROM meta WHERE key='last_updated'").fetchone()
    conn.close()
    return jsonify({"total":total,"cinemas":cc,"movies":mc,"last_updated":lu[0] if lu else None})

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    secret=request.headers.get("X-Secret","")
    if os.environ.get("REFRESH_SECRET") and secret!=os.environ["REFRESH_SECRET"]:
        return jsonify({"error":"unauthorized"}),401
    threading.Thread(target=run_crawl,daemon=True).start()
    return jsonify({"status":"started"})

@app.route("/api/export/excel")
def export_excel():
    import openpyxl
    from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
    df=request.args.get("date_from",date.today().isoformat())
    dt=request.args.get("date_to",  date.today().isoformat())
    cinemas=request.args.getlist("cinema"); mq=request.args.get("movie","").strip()
    conn=get_db()
    sql="SELECT * FROM screenings WHERE date(start_dt) BETWEEN ? AND ?"
    params=[df,dt]
    if cinemas: sql+=f" AND cinema IN ({','.join('?'*len(cinemas))})"; params+=cinemas
    if mq: sql+=" AND movie LIKE ?"; params.append(f"%{mq}%")
    rows=conn.execute(sql+" ORDER BY start_dt",params).fetchall(); conn.close()

    wb=openpyxl.Workbook(); ws=wb.active; ws.title="시간표"
    headers=["극장","영화제목","날짜","시작","종료","런타임(분)","상영관","상영유형","프로그램"]
    thin=Side(style="thin",color="DDDDDD"); bdr=Border(left=thin,right=thin,top=thin,bottom=thin)
    for ci,h in enumerate(headers,1):
        c=ws.cell(row=1,column=ci,value=h)
        c.fill=PatternFill("solid",fgColor="1A1A2E")
        c.font=Font(bold=True,color="FFFFFF",size=11)
        c.alignment=Alignment(horizontal="center"); c.border=bdr
    ws.row_dimensions[1].height=24
    palette=["EFF6FF","FFF7ED","F0FDF4","FFF1F2","F5F3FF","ECFEFF","FFFBEB","F0F9FF"]
    colors={}; pidx=0
    for ri,r in enumerate(rows,2):
        if r["cinema"] not in colors: colors[r["cinema"]]=palette[pidx%len(palette)]; pidx+=1
        fill=PatternFill("solid",fgColor=colors[r["cinema"]])
        sdt=r["start_dt"] or ""
        for col,v in enumerate([r["cinema"],r["movie"],sdt[:10],sdt[11:16],
                (r["end_dt"] or "")[11:16],r["runtime"],r["screen"],r["show_type"],r["program"]],1):
            c=ws.cell(row=ri,column=col,value=v); c.fill=fill; c.border=bdr
            c.alignment=Alignment(vertical="center")
    for col,w in zip("ABCDEFGHI",[16,36,12,10,10,10,14,16,24]):
        ws.column_dimensions[col].width=w
    ws.freeze_panes="A2"
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf,as_attachment=True,
        download_name=f"시간표_{df}_{dt}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

ensure_db()

def initial_crawl_if_empty():
    conn=sqlite3.connect(DB_PATH)
    count=conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0]; conn.close()
    if count==0:
        log.info("DB 비어있음 → 최초 수집 시작 (백그라운드)")
        threading.Thread(target=run_crawl,daemon=True).start()
    else:
        log.info(f"기존 DB: {count}건")

initial_crawl_if_empty()
start_scheduler()

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
