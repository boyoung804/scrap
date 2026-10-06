"""문체부 관련 보도 수집기 (GitHub Actions용)
- 네이버 검색 API로 기사 수집(5분마다) → 처음엔 '온라인'
- 지면기사 검색 화면(PRINT_SEARCH_URL)에서 지면 확인(30분 간격) → '지면' + 면수
- 보고서 날짜 D = (D-1일 07:00 ~ D일 07:00 KST) 범위, data/D.json 에 누적"""
import os, re, json, html, time, datetime as dt
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import requests

KST = dt.timezone(dt.timedelta(hours=9))
QUERIES = ['문화체육관광부', '문체부']
MEDIA = {'chosun.com': '조선일보', 'joongang.co.kr': '중앙일보', 'donga.com': '동아일보', 'hankookilbo.com': '한국일보',
         'khan.co.kr': '경향신문', 'hani.co.kr': '한겨레', 'mk.co.kr': '매일경제', 'hankyung.com': '한국경제',
         'sedaily.com': '서울경제', 'fnnews.com': '파이낸셜뉴스', 'edaily.co.kr': '이데일리', 'segye.com': '세계일보',
         'kmib.co.kr': '국민일보', 'seoul.co.kr': '서울신문', 'etnews.com': '전자신문', 'mt.co.kr': '머니투데이'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'

def report_window():
    now = dt.datetime.now(KST)
    if os.environ.get('REPORT_DATE'):
        d = dt.date.fromisoformat(os.environ['REPORT_DATE'])
    else:
        d = now.date() if now.hour < 7 else now.date() + dt.timedelta(days=1)
    start = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(7), KST)
    return d, start, dt.datetime.combine(d, dt.time(7), KST), now

def art_id(link, orig):
    m = re.search(r'/article/(\d+)/(\d+)|oid=(\d+)&aid=(\d+)', link or '')
    if m:
        return f'{m.group(1)}-{m.group(2)}' if m.group(1) else f'{m.group(3)}-{m.group(4)}'
    return orig

def media_of(url):
    host = (urlparse(url).hostname or '').lower()
    for dom, name in MEDIA.items():
        if host == dom or host.endswith('.' + dom):
            return name
    return ''

def search_api(start, end, now):
    H = {'X-Naver-Client-Id': os.environ['NAVER_ID'], 'X-Naver-Client-Secret': os.environ['NAVER_SECRET']}
    out = {}
    for q in QUERIES:
        for st in (1, 101, 201, 301):
            r = requests.get('https://openapi.naver.com/v1/search/news.json', headers=H, timeout=20,
                             params={'query': q, 'display': 100, 'start': st, 'sort': 'date'})
            r.raise_for_status()
            items = r.json().get('items', [])
            oldest = now
            for it in items:
                pub = parsedate_to_datetime(it['pubDate']).astimezone(KST)
                oldest = min(oldest, pub)
                media = media_of(it.get('originallink') or it.get('link'))
                if not media or not (start <= pub < end):
                    continue
                i = art_id(it.get('link'), it.get('originallink'))
                out[i] = {'id': i, 'title': html.unescape(re.sub(r'<.*?>', '', it['title'])), 'media': media,
                          'url': it.get('originallink') or it.get('link'), 'link': it.get('link'),
                          'pub': pub.isoformat(timespec='minutes'), 'section': '온라인', 'page': ''}
            if not items or oldest < start:
                break
    return out

def print_pages():
    """지면기사 필터 검색 결과에서 {기사id: 면수} 읽기 (네이버 화면 구조에 의존 → 바뀌면 수정 필요)"""
    url = os.environ.get('PRINT_SEARCH_URL', '').strip()
    if not url:
        return {}
    from bs4 import BeautifulSoup
    found = {}
    for st in range(1, 202, 10):
        u = url + ('&' if '?' in url else '?') + f'start={st}'
        r = requests.get(u, headers={'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9'}, timeout=20)
        if r.status_code != 200:
            print('지면 검색 화면 응답', r.status_code); break
        soup = BeautifulSoup(r.text, 'html.parser')
        n = 0
        for a in soup.select('a[href*="news.naver.com"]'):
            i = art_id(a.get('href'), '')
            if not i or i in found:
                continue
            node = a
            for _ in range(8):
                node = node.parent
                if node is None:
                    break
                t = node.get_text(' ', strip=True)
                pm = re.search(r'([A-Za-z]?\d{1,2}면)', t)
                if pm and len(t) < 700:
                    found[i] = pm.group(1); n += 1
                    break
        print(f'지면 화면 {st}~: {n}건')
        if n == 0:
            break
        time.sleep(2)
    return found

def main():
    d, start, end, now = report_window()
    path = f'data/{d}.json'
    data = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {'date': str(d), 'items': []}
    by = {x['id']: x for x in data['items']}
    for i, it in search_api(start, end, now).items():
        by.setdefault(i, it)
    last = data.get('printed_at')
    due = (not last) or (now - dt.datetime.fromisoformat(last)).total_seconds() >= 1800 or os.environ.get('FORCE_PRINT')
    if due:
        pages = print_pages()
        for i, pg in pages.items():
            if i in by:
                by[i]['section'], by[i]['page'] = '지면', pg
        data['printed_at'] = now.isoformat(timespec='seconds')
        print('지면 확정', sum(1 for i in pages if i in by), '건')
    data.update({'from': start.isoformat(), 'to': end.isoformat(), 'updated': now.strftime('%m-%d %H:%M'),
                 'items': sorted(by.values(), key=lambda x: x['pub'])})
    os.makedirs('data', exist_ok=True)
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(path, len(by), '건')

if __name__ == '__main__':
    main()
