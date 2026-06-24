#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import imaplib
import email
import requests
from datetime import datetime, timedelta

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "t4wd2B1t76QPpsxCwCFD")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "P3SyimQip1")

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

DEFAULT_STATE = {
    "news_queries": ["금융 IT 디지털", "핀테크 AI"],
    "seminar_queries": ["금융 IT 세미나 일정", "은행 디지털 세미나"],
}


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_STATE.copy()


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "")


def _naver_search(endpoint, query, display=5, extra_params=None):
    url = f"https://openapi.naver.com/v1/search/{endpoint}"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": display}
    if extra_params:
        params.update(extra_params)
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("items", [])


def search_naver_news(query, display=5):
    return _naver_search("news.json", query, display, {"sort": "date"})


def search_naver_web(query, display=5):
    return _naver_search("webkr.json", query, display)


def _decode_subject(raw):
    from email.header import decode_header
    parts = []
    for s, enc in decode_header(raw or ""):
        if isinstance(s, bytes):
            parts.append(s.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(s)
    return "".join(parts)


def _extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode("utf-8", errors="ignore") if payload else ""


def read_gmail_replies():
    """Read replies to the daily report email from the last 24 hours.

    IMAP SEARCH 인자는 ASCII만 가능하므로(한글 제목으로 검색하면 imaplib가
    인코딩 실패) SINCE 날짜로만 검색하고 제목은 파이썬에서 디코딩해 필터링한다.
    """
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        return []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_pass)
        mail.select("inbox")
        since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        _, data = mail.search(None, "SINCE", since)
        replies = []
        for num in data[0].split():
            _, msg_data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode_subject(msg.get("Subject", ""))
            # 우리 리포트에 대한 '답장(Re:)'만 처리 (원본 발송 메일 제외)
            if "일일 리포트" not in subject:
                continue
            if not subject.strip().lower().startswith("re:"):
                continue
            body = _extract_body(msg)
            # 인용된 원문(>로 시작) 제거
            lines = [l for l in body.split("\n") if not l.lstrip().startswith(">") and l.strip()]
            clean = "\n".join(lines[:30]).strip()
            if clean:
                replies.append(clean)
        mail.logout()
        return replies
    except Exception as e:
        print(f"[에이전트] Gmail 읽기 실패: {e}")
        return []


def update_state_with_claude(replies, current_state):
    """Use Claude to interpret replies and update search queries."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not replies:
        return current_state
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        reply_text = "\n---\n".join(replies)
        prompt = f"""현재 뉴스 검색 쿼리: {json.dumps(current_state['news_queries'], ensure_ascii=False)}
현재 세미나 검색 쿼리: {json.dumps(current_state['seminar_queries'], ensure_ascii=False)}

수신자 요청:
{reply_text}

위 요청을 반영해 검색 쿼리를 업데이트하세요.
JSON만 응답하세요 (설명 없이):
{{"news_queries": [...], "seminar_queries": [...]}}

규칙:
- 기존 쿼리를 기반으로 추가/수정/삭제
- 각 리스트 최대 5개
- 한국어 쿼리"""
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # 코드펜스(```json ... ```) 제거
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        new_state = json.loads(text)
        # 형식 검증: 두 키 모두 리스트여야 함
        if not (isinstance(new_state.get("news_queries"), list)
                and isinstance(new_state.get("seminar_queries"), list)):
            print("[에이전트] Claude 응답 형식 이상, 기존 쿼리 유지")
            return current_state
        return new_state
    except Exception as e:
        print(f"[에이전트] Claude 쿼리 업데이트 실패: {e}")
        return current_state


def fetch_news_items(state):
    seen, results = set(), []
    for query in state.get("news_queries", []):
        try:
            for item in search_naver_news(query, display=4):
                url = item.get("link", "")
                if url not in seen:
                    seen.add(url)
                    results.append(item)
        except Exception as e:
            print(f"[에이전트] 뉴스 검색 실패 ({query}): {e}")
    return results[:8]


def fetch_seminar_items(state):
    seen, results = set(), []
    for query in state.get("seminar_queries", []):
        try:
            for item in search_naver_web(query, display=4):
                url = item.get("link", "")
                if url not in seen:
                    seen.add(url)
                    results.append(item)
        except Exception as e:
            print(f"[에이전트] 세미나 검색 실패 ({query}): {e}")
    return results[:6]


def run_agent():
    """Entry point: read replies → update state → fetch content."""
    state = load_state()

    replies = read_gmail_replies()
    if replies:
        print(f"[에이전트] 답장 {len(replies)}개 발견, 쿼리 업데이트 중...")
        state = update_state_with_claude(replies, state)
        save_state(state)
        print(f"[에이전트] 업데이트된 쿼리: {state}")
    else:
        print("[에이전트] 새 답장 없음, 기존 쿼리 사용")

    news_items = fetch_news_items(state)
    seminar_items = fetch_seminar_items(state)
    print(f"[에이전트] 뉴스 {len(news_items)}건, 세미나 {len(seminar_items)}건 수집")
    return news_items, seminar_items
