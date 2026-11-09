# 투게독 유튜브 로그인 (한 번만): 브라우저에서 nickkim931215 → 채널 "투게독" 선택 → token_togedog.json 저장
# 비밀 파일은 저장소 밖 ~/.togedog/channel 에만 둔다
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

KEYS = Path.home() / ".togedog" / "channel"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

flow = InstalledAppFlow.from_client_secrets_file(str(KEYS / "client_secret.json"), SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=True)

yt = build("youtube", "v3", credentials=creds)
items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
title = items[0]["snippet"]["title"] if items else "(채널 없음)"
print("로그인한 채널:", title, "| 채널 ID:", items[0]["id"] if items else "-")
if title != "투게독":
    print("⚠️ 투게독 채널이 아니에요. 저장하지 않아요. 다시 실행해서 '투게독'을 골라 주세요.")
    sys.exit(1)
if not creds.refresh_token:
    print("⚠️ 갱신 토큰이 없어요. 저장하지 않아요.")
    sys.exit(1)
(KEYS / "token_togedog.json").write_text(creds.to_json(), encoding="utf-8")
print("저장 완료:", KEYS / "token_togedog.json", "| 채널 ID 기록")
(KEYS / "channel.json").write_text(json.dumps({"title": title, "id": items[0]["id"]}, ensure_ascii=False), encoding="utf-8")
