#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
from datetime import datetime, timedelta

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf


def _setup_korean_font():
    try:
        import koreanize_matplotlib  # noqa: F401
        return True
    except Exception:
        pass
    import matplotlib.font_manager as fm
    for name in ("NanumGothic", "Malgun Gothic", "AppleGothic",
                 "Noto Sans CJK KR", "Noto Sans KR"):
        try:
            path = fm.findfont(name, fallback_to_default=False)
            if path:
                plt.rcParams["font.family"] = fm.FontProperties(fname=path).get_name()
                return True
        except Exception:
            continue
    return False


HAS_KR_FONT = _setup_korean_font()

KIS_BASE = os.environ.get("KIS_BASE", "https://openapi.koreainvestment.com:9443")
RECIPIENT = os.environ.get("REPORT_RECIPIENT", "aiwitherickim@gmail.com")
TODAY = datetime.now().strftime("%Y%m%d")
TODAY_KR = datetime.now().strftime("%Y년 %m월 %d일")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

INSTRUMENTS = [
    {"name": "코스피지수",      "en": "KOSPI",                    "code": "0001",      "type": "index"},
    {"name": "아톤",            "en": "Atton (041920)",           "code": "041920",    "type": "stock"},
    {"name": "미래에셋증권",    "en": "Mirae Asset (006800)",     "code": "006800",    "type": "stock"},
    {"name": "TIGER K방산 ETF", "en": "TIGER K-Defense (443480)", "code": "443480",    "type": "stock"},
    {"name": "원달러환율",      "en": "USD/KRW",                  "code": "FX_USDKRW", "type": "fx"},
]


def log(step, ok, msg=""):
    mark = "✅ 성공" if ok else "❌ 실패"
    line = f"[{step}] {mark}"
    if msg:
        line += f" — {msg}"
    print(line, flush=True)


def get_access_token(app_key, app_secret):
    url = f"{KIS_BASE}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    r = requests.post(url, json=body, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 응답에 access_token 없음: {data}")
    return data["access_token"]


def _headers(token, app_key, app_secret, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def fetch_quote(inst, token, app_key, app_secret):
    if inst["type"] == "index":
        url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": inst["code"]}
        r = requests.get(url, headers=_headers(token, app_key, app_secret, "FHPUP02100000"),
                         params=params, timeout=20)
        r.raise_for_status()
        o = r.json().get("output", {})
        return {
            "name": inst["name"],
            "price": o.get("bstp_nmix_prpr"),
            "diff": o.get("bstp_nmix_prdy_vrss"),
            "sign": o.get("prdy_vrss_sign"),
            "rate": o.get("bstp_nmix_prdy_ctrt"),
        }
    elif inst["type"] == "stock":
        url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": inst["code"]}
        r = requests.get(url, headers=_headers(token, app_key, app_secret, "FHKST01010100"),
                         params=params, timeout=20)
        r.raise_for_status()
        o = r.json().get("output", {})
        return {
            "name": o.get("hts_kor_isnm") or inst["name"],
            "price": o.get("stck_prpr"),
            "diff": o.get("prdy_vrss"),
            "sign": o.get("prdy_vrss_sign"),
            "rate": o.get("prdy_ctrt"),
        }
    elif inst["type"] == "fx":
        return {"name": inst["name"], "price": None, "diff": None,
                "sign": None, "rate": None, "note": "KIS 국내주식 API 미지원"}
    else:
        raise ValueError(f"알 수 없는 type: {inst['type']}")


def fetch_daily_ohlcv(inst, token, app_key, app_secret, days=35):
    if inst["type"] == "fx":
        return None
    end = datetime.now()
    start = end - timedelta(days=days)
    div = "U" if inst["type"] == "index" else "J"
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": div,
        "FID_INPUT_ISCD": inst["code"],
        "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    r = requests.get(url, headers=_headers(token, app_key, app_secret, "FHKST03010100"),
                     params=params, timeout=20)
    r.raise_for_status()
    rows = r.json().get("output2", []) or []
    if not rows:
        return None
    if inst["type"] == "index":
        o_key, h_key, l_key, c_key = ("bstp_nmix_oprc", "bstp_nmix_hgpr",
                                       "bstp_nmix_lwpr", "bstp_nmix_prpr")
    else:
        o_key, h_key, l_key, c_key = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr")
    recs = []
    for row in rows:
        d = row.get("stck_bsop_date")
        if not d:
            continue
        try:
            recs.append({
                "Date": datetime.strptime(d, "%Y%m%d"),
                "Open": float(row.get(o_key) or 0),
                "High": float(row.get(h_key) or 0),
                "Low": float(row.get(l_key) or 0),
                "Close": float(row.get(c_key) or 0),
                "Volume": float(row.get("acml_vol") or 0),
            })
        except ValueError:
            continue
    if not recs:
        return None
    return pd.DataFrame(recs).set_index("Date").sort_index()


def build_combined_chart(charts, out_path):
    plt.rcParams["axes.unicode_minus"] = False
    mc = mpf.make_marketcolors(up="red", down="blue", edge="inherit",
                               wick={"up": "red", "down": "blue"},
                               volume={"up": "red", "down": "blue"})
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle=":")
    fig = plt.figure(figsize=(18, 11))
    gs = fig.add_gridspec(4, 3, height_ratios=[3, 1, 3, 1], hspace=0.45, wspace=0.18)
    drawn = 0
    for i, (inst, df) in enumerate(charts):
        block, col = divmod(i, 3)
        ax_price = fig.add_subplot(gs[block * 2, col])
        ax_vol = fig.add_subplot(gs[block * 2 + 1, col], sharex=ax_price)
        title = inst["name"] if HAS_KR_FONT else inst["en"]
        if df is None or df.empty:
            ax_vol.set_visible(False)
            ax_price.text(0.5, 0.5, f"{title}\n(데이터 없음)",
                          ha="center", va="center", fontsize=13)
            ax_price.set_xticks([]); ax_price.set_yticks([])
            continue
        rng = f"{df.index[0].strftime('%m/%d')} ~ {df.index[-1].strftime('%m/%d')}"
        mpf.plot(df, type="candle", style=style, ax=ax_price, volume=ax_vol,
                 axtitle=f"{title}  [{rng}]", datetime_format="%m/%d",
                 xrotation=0, warn_too_much_data=10000)
        drawn += 1
    fig.suptitle(f"Daily Candlestick (1M)  -  {datetime.now().strftime('%Y-%m-%d')}",
                 fontsize=17, y=0.96)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return drawn


def build_email_html(quotes, chart_cid):
    def fmt(q):
        if q.get("price") is None:
            note = q.get("note", "N/A")
            return (f"<tr><td>{q['name']}</td><td colspan='3' "
                    f"style='color:#888'>{note}</td></tr>")
        sign = q.get("sign")
        color = "#d32f2f" if sign in ("1", "2") else ("#1565c0" if sign in ("4", "5") else "#555")
        arrow = "▲" if sign in ("1", "2") else ("▼" if sign in ("4", "5") else "－")
        return (f"<tr><td>{q['name']}</td>"
                f"<td style='text-align:right'>{q['price']}</td>"
                f"<td style='text-align:right;color:{color}'>{arrow} {q['diff']}</td>"
                f"<td style='text-align:right;color:{color}'>{q['rate']}%</td></tr>")
    rows = "\n".join(fmt(q) for q in quotes)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;color:#222;max-width:760px;margin:0 auto">
  <h2>📈 주식 시황 &amp; 금융 IT 리포트 — {TODAY_KR}</h2>
  <h3>📈 주식 시황</h3>
  <table border="1" cellspacing="0" cellpadding="8" style="border-collapse:collapse;width:100%;font-size:14px">
    <thead style="background:#f2f4f8">
      <tr><th align="left">종목</th><th>현재가</th><th>전일比</th><th>등락률</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <h3>📊 차트 (최근 1개월 일봉)</h3>
  <img src="cid:{chart_cid}" alt="합본 캔들차트" style="width:100%;max-width:760px;border:1px solid #eee"/>
  <p style="color:#999;font-size:12px;margin-top:24px">
    ※ 뉴스레터(금융 IT/세미나) 섹션은 이번 테스트에서 제외되었습니다.<br/>
    ※ 본 메일은 테스트 발송입니다. 운영 전환 시 수신자를 yoonjin1964@gmail.com 으로 변경하세요.
  </p>
</body></html>"""


def send_email(subject, html_body, chart_path, chart_cid, recipient):
    smtp_user = os.environ.get("GMAIL_USER")
    smtp_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not smtp_user or not smtp_pass:
        log("7.메일 발송", False, "GMAIL_USER / GMAIL_APP_PASSWORD 미설정")
        return False
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    alt = MIMEMultipart("alternative")
    msg.attach(alt)
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    if chart_path and os.path.exists(chart_path):
        with open(chart_path, "rb") as f:
            img = MIMEImage(f.read())
        img.add_header("Content-ID", f"<{chart_cid}>")
        img.add_header("Content-Disposition", "inline", filename=os.path.basename(chart_path))
        msg.attach(img)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(smtp_user, recipient, msg.as_bytes())
        return True
    except Exception as e:
        log("7.메일 발송", False, repr(e))
        return False


def main():
    print("=" * 60)
    print(f"  데일리 리포트 생성 시작 — {TODAY_KR}  (수신자: {RECIPIENT})")
    print("=" * 60)
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        log("0.환경변수", False, "KIS_APP_KEY / KIS_APP_SECRET 미설정")
        sys.exit(2)
    try:
        token = get_access_token(app_key, app_secret)
        log("1.KIS 토큰", True)
    except Exception as e:
        log("1.KIS 토큰", False, repr(e))
        sys.exit(1)
    quotes = []
    for inst in INSTRUMENTS:
        try:
            q = fetch_quote(inst, token, app_key, app_secret)
            quotes.append(q)
            detail = q.get("note") or f"{q.get('price')} ({q.get('rate')}%)"
            log(f"2.시세 {inst['name']}", q.get("price") is not None or inst["type"] == "fx", detail)
        except Exception as e:
            quotes.append({"name": inst["name"], "price": None, "note": f"수신실패: {e}"})
            log(f"2.시세 {inst['name']}", False, repr(e))
        time.sleep(0.3)
    charts = []
    for inst in INSTRUMENTS:
        try:
            df = fetch_daily_ohlcv(inst, token, app_key, app_secret)
            charts.append((inst, df))
            if inst["type"] == "fx":
                log(f"3.일봉 {inst['name']}", True, "FX 차트 생략")
            else:
                log(f"3.일봉 {inst['name']}", df is not None,
                    f"{len(df)}봉" if df is not None else "데이터 없음")
        except Exception as e:
            charts.append((inst, None))
            log(f"3.일봉 {inst['name']}", False, repr(e))
        time.sleep(0.3)
    chart_path = os.path.join(OUT_DIR, f"combined_chart_{TODAY}.png")
    try:
        drawn = build_combined_chart(charts, chart_path)
        log("4.차트 생성", drawn > 0, f"{drawn}개 차트 -> {os.path.basename(chart_path)}")
    except Exception as e:
        log("4.차트 생성", False, repr(e))
        chart_path = None
    chart_cid = "combined_chart"
    html = build_email_html(quotes, chart_cid)
    html_path = os.path.join(OUT_DIR, f"email_body_{TODAY}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log("5.HTML 본문", True, os.path.basename(html_path))
    meta = {
        "date": TODAY,
        "recipient": RECIPIENT,
        "subject": f"[일일 리포트] 주식 시황 & 금융 IT 뉴스 - {TODAY_KR}",
        "html_path": html_path,
        "chart_path": chart_path,
        "chart_cid": chart_cid,
        "quotes": quotes,
    }
    meta_path = os.path.join(OUT_DIR, f"report_data_{TODAY}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log("6.메타 저장", True, os.path.basename(meta_path))
    subject = f"[일일 리포트] 주식 시황 & 금융 IT 뉴스 - {TODAY_KR}"
    ok = send_email(subject, html, chart_path, chart_cid, RECIPIENT)
    log("7.메일 발송", ok, f"-> {RECIPIENT}" if ok else "발송 실패")
    print("=" * 60)
    print(f"  - 차트: {chart_path}")
    print(f"  - 본문: {html_path}")
    print(f"  - 메타: {meta_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
