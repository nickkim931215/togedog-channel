"""YouTube 업로드. 채널별 최초 1회 브라우저 로그인 → token_<채널>.json 저장 → 이후 자동 갱신(refresh)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE = Path(__file__).resolve().parent
CLIENT_SECRET = BASE / "client_secret.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # 업로드 대상 채널 확인용
]


def token_path(channel: str) -> Path:
    return BASE / f"token_{ {'투게독': 'togedog'}.get(channel, channel)}.json"  # 파일 이름은 영문으로


def get_credentials(channel: str) -> Credentials:
    token = token_path(channel)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError:
            print("토큰 갱신 실패(만료/취소됨) → 다시 로그인합니다.")
    if os.environ.get("CI"):
        raise SystemExit(f"[{channel}] 유튜브 로그인 토큰이 없거나 만료됐습니다. PC에서 다시 로그인한 뒤 "
                         "GitHub Secret YT_TOKEN_TOGEDOG 를 갱신하세요.")
    print(f"브라우저 로그인 시 채널 선택 화면에서 '{channel}' 채널을 고르세요.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def check_channel(youtube, channel: str):
    """로그인된 채널이 기대한 채널인지 확인. 다르면 토큰을 지우고 중단."""
    items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
    snip = items[0]["snippet"] if items else {}
    name, handle = snip.get("title", ""), snip.get("customUrl", "")
    norm = lambda s: s.lower().replace("@", "").replace("_", "").replace(" ", "")
    if norm(channel) not in (norm(name), norm(handle)):
        token_path(channel).unlink(missing_ok=True)
        raise SystemExit(f"로그인된 채널이 '{name}' ({handle}) 입니다. '{channel}'이 아니므로 업로드를 중단하고 "
                         f"토큰을 삭제했습니다. 다시 실행해 '{channel}' 채널을 선택하세요.")
    print(f"  대상 채널 확인: {name} ({handle})")


def upload_video(path, title, channel, description="", tags=None, privacy="private", publish_at=None) -> str:
    """publish_at(datetime, 시간대 포함)을 주면 비공개로 올리고 그 시각에 유튜브가 자동 공개.
    이미 지난 시각이면 바로 공개로 올림."""
    if publish_at and publish_at <= datetime.now(timezone.utc) + timedelta(minutes=3):
        print(f"  공개 예정 시각({publish_at:%m/%d %H:%M})이 지나서 바로 공개합니다.")
        publish_at, privacy = None, "public"
    if publish_at:
        privacy = "private"  # 예약 공개는 반드시 private + publishAt
    from office import KST, board, report
    report("publish", "유튜브에 올리는 중이에요 📤", "youtube", stage="publish")
    youtube = build("youtube", "v3", credentials=get_credentials(channel))
    check_channel(youtube, channel)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "22",
            "defaultLanguage": "ko",
        },
        # containsSyntheticMedia: AI 목소리(실제 사람 목소리 복제 포함) 사용 → 유튜브 'AI 합성 콘텐츠' 자진 표시 (정책 위험 감소)
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=-1, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"  업로드 {int(status.progress() * 100)}%")
    vid = resp["id"]
    report("publish", "업로드 100%! 공개 설정 확인할게요", "youtube")
    print(f"업로드 완료 (요청: {privacy}): https://youtube.com/shorts/{vid}")
    items = youtube.videos().list(part="status", id=vid).execute().get("items", [])
    st = items[0]["status"] if items else {}
    actual = st.get("privacyStatus", "unknown")
    if publish_at:
        if not st.get("publishAt"):
            raise SystemExit(f"⚠ 예약 공개 설정이 적용되지 않았습니다 (영상 {vid}, 상태 {actual}). 스튜디오에서 확인하세요.")
        print(f"  예약 공개 확인: {publish_at:%m/%d %H:%M} 공개 예정 ({st['publishAt']})")
        board()
        report("publish", f"대표님! {publish_at.astimezone(KST):%m/%d %H:%M} 공개 예약 완료했습니다 🎉", "youtube",
               kind="report", video_id=vid)
        return vid
    elif actual != privacy:
        print(f"  ⚠ 실제 공개 상태: {actual} — 미심사 API 프로젝트라 유튜브가 비공개로 잠갔을 수 있습니다. "
              "YouTube API 감사(Audit) 승인 후 해결됩니다.")
    else:
        print(f"  실제 공개 상태 확인: {actual}")
    board()
    report("publish", f"대표님! 영상 업로드({actual}) 완료했습니다 🎉", "youtube", kind="report", video_id=vid)
    return vid
