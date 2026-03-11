# test_poster.py
import requests
from bs4 import BeautifulSoup

url = "https://www.dtryx.com/movie/view.do?cgid=FE8EF4D2-F22D-4802-A39A-D58F23A29C1E&MovieCd=019269"
headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
res = requests.get(url, headers=headers, timeout=10)
soup = BeautifulSoup(res.text, "html.parser")

# og:image 확인
og = soup.find("meta", property="og:image")
print("og:image:", og["content"] if og else "없음")

# img 태그 전체 출력
for img in soup.find_all("img")[:10]:
    print("img src:", img.get("src",""))