import os
import re
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build

from linebot.v3.messaging import (
    MessagingApi, Configuration, ApiClient,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from googleapiclient.errors import HttpError

# ====== Render対応: サービスアカウントJSONを環境変数から生成 ======
CREDENTIALS_JSON = os.environ.get("CREDENTIALS_JSON", "").strip()
if CREDENTIALS_JSON:
    with open("credentials.json", "w", encoding="utf-8") as f:
        f.write(CREDENTIALS_JSON)

# ====== 環境変数からLINEの情報を取得 ======
CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"].strip()
CHANNEL_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip()

print("SECRET_LEN:", len(CHANNEL_SECRET))
print("TOKEN_LEN :", len(CHANNEL_TOKEN))

# ====== LINE v3 クライアント ======
config = Configuration(access_token=CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ====== Google Calendar 設定 ======
SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("CALENDAR_ID", "primary")

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
calendar_service = build("calendar", "v3", credentials=creds)

# -------------------------
# 日付解析 (10/3, 2025/10/3, 10月3日 など)
# -------------------------
def _parse_explicit_date(text):
    text = text.strip()
    ymd = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*(.*)$", text)
    if ymd:
        y, m, d, rest = ymd.groups()
        return datetime.date(int(y), int(m), int(d)), rest.strip()

    md = re.match(r"^(\d{1,2})[/-](\d{1,2})\s*(.*)$", text)
    if md:
        m, d, rest = md.groups()
        y = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).year
        return datetime.date(y, int(m), int(d)), rest.strip()

    jp = re.match(r"^(\d{1,2})月(\d{1,2})日\s*(.*)$", text)
    if jp:
        m, d, rest = jp.groups()
        y = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).year
        return datetime.date(y, int(m), int(d)), rest.strip()

    return None, text

# -------------------------
# 予定登録（日付/今日/明日）
# -------------------------
def add_simple_event_jp(text):
    tz = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(tz)

    # ① 日付指定パターン
    explicit_date, rest = _parse_explicit_date(text)
    if explicit_date:
        # 終日
        m = re.match(r"^終日\s+(.+)$", rest)
        if m:
            title = m.group(1)
            body = {
                "summary": title,
                "start": {"date": explicit_date.isoformat()},
                "end": {"date": (explicit_date + datetime.timedelta(days=1)).isoformat()},
            }
            calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
            return f"{explicit_date.strftime('%m/%d')} 終日『{title}』を登録しました。"

        # 時間範囲
        m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s+(.+)$", rest)
        if m:
            sh, sm, eh, em, title = m.groups()
            start = datetime.datetime.combine(explicit_date, datetime.time(int(sh), int(sm), tzinfo=tz))
            end = datetime.datetime.combine(explicit_date, datetime.time(int(eh), int(em), tzinfo=tz))
            body = {
                "summary": title,
                "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
                "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
            }
            calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
            return f"{explicit_date.strftime('%m/%d')} {sh}:{sm}〜{eh}:{em}『{title}』を登録しました。"

        # 単一開始＋長さ
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?時?\s*(\d+)?分?\s+(.+)$", rest)
        if m:
            hour, minute, dur, title = m.groups()
            minute = int(minute) if minute else 0
            dur = int(dur) if dur else 60
            start = datetime.datetime.combine(explicit_date, datetime.time(int(hour), minute, tzinfo=tz))
            end = start + datetime.timedelta(minutes=dur)
            body = {
                "summary": title,
                "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
                "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
            }
            calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
            return f"{explicit_date.strftime('%m/%d')} {int(hour)}:{minute:02d}〜{dur}分『{title}』を登録しました。"

        return None

    # ② 今日/明日パターン
    m = re.match(r"(今日|明日)\s*終日\s*(.+)", text)
    if m:
        day_word, title = m.groups()
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        body = {
            "summary": title,
            "start": {"date": date.isoformat()},
            "end": {"date": (date + datetime.timedelta(days=1)).isoformat()},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word} 終日『{title}』を登録しました。"

    m = re.match(r"(今日|明日)\s*(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*(.+)", text)
    if m:
        day_word, sh, sm, eh, em, title = m.groups()
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        start = datetime.datetime.combine(date, datetime.time(int(sh), int(sm), tzinfo=tz))
        end = datetime.datetime.combine(date, datetime.time(int(eh), int(em), tzinfo=tz))
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word}{sh}:{sm}〜{eh}:{em}『{title}』を登録しました。"

    m = re.match(r"(今日|明日)\s*(\d{1,2})(?::(\d{2}))?時?\s*(\d+)?分?\s*(.+)", text)
    if m:
        day_word, hour, minute, dur, title = m.groups()
        minute = int(minute) if minute else 0
        dur = int(dur) if dur else 60
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        start = datetime.datetime.combine(date, datetime.time(int(hour), minute, tzinfo=tz))
        end = start + datetime.timedelta(minutes=dur)
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word}{hour}:{minute:02d}〜{dur}分『{title}』を登録しました。"

    return None

# -------------------------
# 予定一覧/削除/ヘルプ（省略せず従来通り残す）
# -------------------------
# …（ここは前のバージョンの list_events, delete_event, get_help_text, Flask 部分をそのまま使ってください）
