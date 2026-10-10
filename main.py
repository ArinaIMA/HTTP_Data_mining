import re
import time
import requests
from bs4 import BeautifulSoup

USER_ID = "8d7b631872f38aebba9000ed8ac3550c"
BASE = "http://hw1.alexbers.com"

s = requests.Session()
s.cookies.set("user", USER_ID)

MAXVAL = 10
TIMEOUT = 20
RETRIES = 3
RETRY_DELAY = 2

MARKERS = [
    ("cookie", "cookies"),
    ("заголовк", "headers"),
    ("параметры запроса", "params"),
    ("данные формы", "form"),
    ("содержимым", "files"),
]

def shorten(v, n=MAXVAL):
    v = str(v)
    return v if len(v) <= n else v[:n] + "…"


def table_to_dict(table):
    d = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 2:
            k = cells[0].get_text(strip=True)
            v = cells[1].get_text(strip=True)
            if k in ("Ключ", "Имя файла", "Key", "Name"):
                continue
            if k:
                d[k] = v
    return d


def print_table(title, d):
    if not d:
        print(f"  {title}: —")
        return
    print(f"  {title}:")
    w = max(len(k) for k in d)
    for k, v in d.items():
        print(f"    {k:<{w}} = {shorten(v)}")

def extract_task(soup, text):
    info = {
        "type": None, "url": None, "link": None,
        "cookies": {}, "headers": {}, "params": {},
        "form": {}, "files": {},
    }

    m = re.search(r'(?:POST|GET)[-\s]*запрос\s*по\s*адресу\s*(/\S+)', text, re.I)
    if not m:
        m = re.search(r'Загрузите файлы по адресу\s*(/\S+)', text, re.I)
    if not m:
        m = re.search(r'по адресу\s*(/\S+)', text, re.I)
    if m:
        info["url"] = m.group(1).rstrip(".,")

    low = text.lower()
    if "загрузите файлы" in low:
        info["type"] = "upload"
    elif "post-запрос" in low:
        info["type"] = "post"
    elif "get-запрос" in low:
        info["type"] = "get"
    elif "перейдите по" in low and "ссылке" in low:
        info["type"] = "link"

    html = str(soup)

    tables_with_pos = []
    for t in soup.find_all("table"):
        pos = html.find(str(t))
        tables_with_pos.append((pos, t))
    tables_with_pos.sort(key=lambda x: x[0])

    marker_positions = []
    for phrase, key in MARKERS:
        for mm in re.finditer(phrase, html, re.I):
            marker_positions.append((mm.start(), key))
    marker_positions.sort()

    for tpos, t in tables_with_pos:
        prev_key = None
        for mpos, key in marker_positions:
            if mpos < tpos:
                prev_key = key
            else:
                break
        if prev_key is None:
            continue
        d = table_to_dict(t)
        if not d:
            continue
        info[prev_key].update(d)

    if info["type"] == "link":
        a = None
        for cand in soup.find_all("a", href=True):
            if "ссылк" in cand.get_text(strip=True).lower():
                a = cand
                break
        if a is None:
            a = soup.find("a", href=True)
        if a:
            info["link"] = a["href"]

    return info


def safe_request(s, method, url, **kwargs):
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("Connection", "close")

    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            if method == "GET":
                return s.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
            else:
                return s.post(url, headers=headers, timeout=TIMEOUT, **kwargs)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_exc = e
            # print(f"ошибка при отправке запроса")
            time.sleep(RETRY_DELAY)
    raise last_exc


def do_step(s, info):
    if info["type"] == "link" and info["link"]:
        return safe_request(
            s, "GET", BASE + info["link"],
            params=info["params"],
            cookies=info["cookies"],
            headers=info["headers"],
        )

    if not info["url"]:
        return None
    url = BASE + info["url"]

    if info["type"] == "get":
        return safe_request(
            s, "GET", url,
            params=info["params"],
            cookies=info["cookies"],
            headers=info["headers"],
        )

    if info["type"] == "post":
        return safe_request(
            s, "POST", url,
            data=info["form"],
            params=info["params"],
            cookies=info["cookies"],
            headers=info["headers"],
        )

    if info["type"] == "upload":
        files = {name: (name, content, "application/octet-stream")
                 for name, content in info["files"].items()}
        return safe_request(
            s, "POST", url,
            files=files,
            params=info["params"],
            cookies=info["cookies"],
            headers=info["headers"],
        )

    return None


def find_token(text):
    m = re.search(r'(?:token|токен|ключ|key)[^\w]{0,20}([A-Za-z0-9_\-]{16,})', text, re.I)
    if m:
        return m.group(1)
    m = re.search(r'\b([a-f0-9]{32,})\b', text)
    if m:
        return m.group(1)
    return None


def get_step_number(text):
    m = re.search(r'Шаг\s*#(\d+)', text)
    return int(m.group(1)) if m else None


def main():
    r = s.get(BASE + "/")

    expected_step = None
    max_seen = 0

    for iteration in range(10000):
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        current_step = get_step_number(text)

        if current_step is not None:
            if max_seen > 1 and current_step <= max_seen:
                if current_step < max_seen or (
                    expected_step is not None and current_step < expected_step
                ):
                    print(f"\n построен неверный запрос")
                    print("--- страница ---")
                    print(text[:800])
                    return
            max_seen = max(max_seen, current_step)

        token = find_token(text)
        if token:
            print("\nтокен:", token, '\n')
            return

        info = extract_task(soup, text)

        '''
        print(f"\nшаг {current_step} (итерация {iteration})")
        print("--- начало страницы ---")
        for line in text.splitlines():
            line = line.strip()
            if line:
                print("  " + line)
        print("--- конец страницы ---\n\n")

        print(f"Метод: {info['type']} | путь: {info['url'] or info['link']}")
        print_table("Cookies", info["cookies"])
        print_table("Headers", info["headers"])
        print_table("Params ", info["params"])
        print_table("Form   ", info["form"])
        
        if info["files"]:
            print_table("Files  ", info["files"])
        '''


        if info["type"] is None:
            print("не распознан шаг - что-то новенькое")
            return

        expected_step = (current_step + 1) if current_step is not None else None

        try:
            r = do_step(s, info)
        except Exception as e:
            print(f"шаг не выполнен: {type(e).__name__}: {e}")
            time.sleep(5)
            try:
                r = do_step(s, info)
            except Exception as e2:
                print(f"повторный сбой: {type(e2).__name__}: {e2}")
                return
        if r is None:
            print("не получилось")
            return

    print("Лимит шагов исчерпан")


if __name__ == "__main__":
    main()