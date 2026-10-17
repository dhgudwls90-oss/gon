# -*- coding: utf-8 -*-
"""
DART 공시 알림 텔레그램 봇
- OpenDART API로 지정한 회사의 새 공시를 주기적으로 확인하고
- 새 공시가 올라오면 텔레그램으로 알려줍니다.
- 파이썬 기본 라이브러리만 사용합니다 (추가 설치 불필요).

사용법
  python dart_telegram_bot.py            # 봇 실행 (계속 감시)
  python dart_telegram_bot.py --chatid   # 내 텔레그램 chat_id 확인
  python dart_telegram_bot.py --test     # 텔레그램 테스트 메시지 + 오늘 공시 확인
"""
import datetime as dt
import html
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
SEEN_PATH = os.path.join(BASE, "seen.json")
CORP_CACHE_PATH = os.path.join(BASE, "corp_codes.json")
KST = dt.timezone(dt.timedelta(hours=9))

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


def log(msg):
    print(f"[{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def http_get(url, params=None, timeout=30):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "dart-telegram-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------- 설정 ----------------
def load_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None and os.environ.get("DART_API_KEY"):
        # GitHub Actions 등: 환경변수(Secrets)에서 설정 읽기
        cfg = {
            "dart_api_key": os.environ["DART_API_KEY"],
            "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        }
        if os.environ.get("COMPANIES"):
            cfg["companies"] = [c.strip() for c in os.environ["COMPANIES"].split(",") if c.strip()]
    if cfg is None:
        sys.exit(f"설정 파일이 없습니다: {CONFIG_PATH}\n"
                 "config.example.json을 config.json으로 복사한 뒤 값을 채워 주세요.")
    for key in ("dart_api_key", "telegram_bot_token"):
        if not cfg.get(key) or "여기에" in str(cfg.get(key)):
            sys.exit(f"config.json의 '{key}' 값을 입력해 주세요.")
    cfg.setdefault("companies", ["삼성전자", "SK하이닉스", "리브스메드"])
    cfg.setdefault("interval_sec", 60)
    cfg.setdefault("night_interval_sec", 300)
    return cfg


# ---------------- 텔레그램 ----------------
def tg_send(cfg, text):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": cfg["telegram_chat_id"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {res}")


def show_chat_ids(cfg):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/getUpdates"
    res = json.loads(http_get(url))
    chats = {}
    for upd in res.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name")]))
            chats[chat["id"]] = name
    if not chats:
        print("메시지를 찾지 못했습니다. 텔레그램에서 내 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
    for cid, name in chats.items():
        print(f"chat_id: {cid}   ({name})")


# ---------------- DART ----------------
def resolve_corp_codes(cfg):
    """회사 이름 -> DART 고유번호(corp_code). 한 번 받은 목록은 corp_codes.json에 저장."""
    cache = load_json(CORP_CACHE_PATH, {})
    names = cfg["companies"]
    if all(n in cache for n in names):
        return {n: cache[n] for n in names}

    log("DART 회사 고유번호 목록을 내려받는 중...")
    raw = http_get(DART_CORPCODE_URL, {"crtfc_key": cfg["dart_api_key"]}, timeout=120)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        sys.exit(f"고유번호 목록을 받지 못했습니다. API 키를 확인하세요.\n응답: {raw[:300]!r}")
    root = ET.fromstring(zf.read(zf.namelist()[0]))

    found = {}
    for item in root.iter("list"):
        name = (item.findtext("corp_name") or "").strip()
        if name in names:
            stock = (item.findtext("stock_code") or "").strip()
            # 같은 이름이 여러 개면 상장사(종목코드 있는 쪽)를 우선
            if name not in found or (stock and not found[name]["stock_code"]):
                found[name] = {"corp_code": item.findtext("corp_code").strip(),
                               "stock_code": stock}
    missing = [n for n in names if n not in found]
    if missing:
        sys.exit(f"DART에서 회사를 찾지 못했습니다: {missing}\n"
                 "DART에 등록된 정확한 회사명으로 config.json을 고쳐 주세요.")
    cache.update(found)
    save_json(CORP_CACHE_PATH, cache)
    for n in names:
        log(f"  {n}: 고유번호 {found[n]['corp_code']} (종목코드 {found[n]['stock_code'] or '-'})")
    return {n: found[n] for n in names}


def fetch_filings(cfg, corp_code):
    """어제~오늘 접수된 공시 목록."""
    today = dt.datetime.now(KST).date()
    params = {
        "crtfc_key": cfg["dart_api_key"],
        "corp_code": corp_code,
        "bgn_de": (today - dt.timedelta(days=1)).strftime("%Y%m%d"),
        "end_de": today.strftime("%Y%m%d"),
        "page_count": 100,
    }
    res = json.loads(http_get(DART_LIST_URL, params))
    status = res.get("status")
    if status == "013":          # 조회된 데이터 없음
        return []
    if status != "000":
        raise RuntimeError(f"DART 오류 {status}: {res.get('message')}")
    return res.get("list", [])


def format_msg(f):
    rm = f.get("rm", "").strip()
    tags = {"유": "유가증권", "코": "코스닥", "정": "정정", "연": "연결"}
    tag_txt = " ".join(tags.get(c, c) for c in rm) if rm else ""
    d = f.get("rcept_dt", "")
    date_txt = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
    lines = [
        f"📢 <b>{html.escape(f['corp_name'])}</b> 새 공시",
        f"<b>{html.escape(f['report_nm'].strip())}</b>",
        f"제출인: {html.escape(f.get('flr_nm', ''))}",
        f"접수일: {date_txt}" + (f"  [{html.escape(tag_txt)}]" if tag_txt else ""),
        f'<a href="{DART_VIEW_URL.format(f["rcept_no"])}">공시 원문 보기</a>',
    ]
    return "\n".join(lines)


# ---------------- 메인 루프 ----------------
def check_once(cfg, corps, seen, notify=True):
    new_items = []
    for name, info in corps.items():
        try:
            for f in fetch_filings(cfg, info["corp_code"]):
                if f["rcept_no"] not in seen:
                    new_items.append(f)
        except Exception as e:
            log(f"{name} 조회 실패: {e}")
        time.sleep(0.3)

    new_items.sort(key=lambda f: f["rcept_no"])   # 접수번호 순 = 시간 순
    for f in new_items:
        if notify:
            try:
                tg_send(cfg, format_msg(f))
                log(f"알림 전송: {f['corp_name']} - {f['report_nm'].strip()}")
            except Exception as e:
                log(f"텔레그램 전송 실패(다음 확인 때 재시도): {e}")
                continue
        seen[f["rcept_no"]] = f["rcept_dt"]
    if new_items:
        # 오래된 기록 정리 (7일 지난 것 삭제)
        cutoff = (dt.datetime.now(KST).date() - dt.timedelta(days=7)).strftime("%Y%m%d")
        for k in [k for k, v in seen.items() if v < cutoff]:
            del seen[k]
        save_json(SEEN_PATH, seen)
    return new_items


def main():
    cfg = load_config()

    if "--chatid" in sys.argv:
        show_chat_ids(cfg)
        return
    if not cfg.get("telegram_chat_id"):
        sys.exit("config.json의 'telegram_chat_id'를 입력하세요. "
                 "(모르면: python dart_telegram_bot.py --chatid)")

    corps = resolve_corp_codes(cfg)

    if "--test" in sys.argv:
        tg_send(cfg, "✅ DART 공시 알림 봇 테스트 메시지입니다.")
        log("텔레그램 테스트 메시지 전송 완료")
        for name, info in corps.items():
            items = fetch_filings(cfg, info["corp_code"])
            log(f"{name}: 어제~오늘 공시 {len(items)}건")
            for f in items[:3]:
                log(f"   - {f['rcept_dt']} {f['report_nm'].strip()}")
        return

    seen = load_json(SEEN_PATH, None)
    if seen is None:
        # 첫 실행: 이미 올라와 있는 공시는 알림 없이 '본 것'으로 처리
        seen = {}
        check_once(cfg, corps, seen, notify=False)
        save_json(SEEN_PATH, seen)
        log(f"첫 실행: 기존 공시 {len(seen)}건을 기록했습니다. 이제부터 새 공시만 알립니다.")
        if "--once" in sys.argv:
            tg_send(cfg, "🤖 DART 공시 알림 봇 설정 완료\n감시 대상: " + ", ".join(corps))
            return

    if "--once" in sys.argv:
        # 한 번만 확인하고 종료 (GitHub Actions 예약 실행용)
        new = check_once(cfg, corps, seen)
        log(f"확인 완료: 새 공시 {len(new)}건")
        return

    names = ", ".join(corps)
    try:
        tg_send(cfg, f"🤖 DART 공시 알림 봇 시작\n감시 대상: {names}")
    except Exception as e:
        sys.exit(f"텔레그램 전송 실패. 토큰과 chat_id를 확인하세요.\n{e}")
    log(f"감시 시작: {names}")

    while True:
        check_once(cfg, corps, seen)
        hour = dt.datetime.now(KST).hour
        interval = cfg["interval_sec"] if 6 <= hour < 22 else cfg["night_interval_sec"]
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("종료합니다.")
