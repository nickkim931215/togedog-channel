"""자가 점검 — 문제 있을 때만 대표님 디스코드로 알림 (매일 health.yml이 실행).

  python healthcheck.py                 # 전체 점검: 예약 대기열·유튜브 로그인·Gemini 대본/목소리·Pixabay·ElevenLabs
  python healthcheck.py --after-failure # 제작 실패 직후: 급한 것(곧 공개될 회차 비었나·로그인)만
  python healthcheck.py --weekly        # 전체 점검 + 문제 없어도 주간 보고 (알림이 살아 있다는 신호)
"""
import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import alert
from autopilot import KST, PUBLISH_TIMES, load_history
from settings import load_env

REPO = "https://github.com/nickkim931215/togedog-channel"
STUDIO = "https://studio.youtube.com/"
ASK = "👉 Claude에게 **\"투게독 불러와\"** 하고 이 메시지를 보여주시면 고쳐드려요."


def empty_slots(hours: float) -> list[datetime]:
    """앞으로 hours 안에 공개돼야 하는데 예약 업로드가 안 된 회차."""
    now = datetime.now(KST)
    done = {h["publish_at"] for h in load_history() if h.get("publish_at") and h.get("video_id")}
    out = []
    for day in range(3):
        d = (now + timedelta(days=day)).date()
        for hh, mm in PUBLISH_TIMES.values():
            at = datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)
            if now < at <= now + timedelta(hours=hours) and at.isoformat() not in done:
                out.append(at)
    return sorted(out)


def check_youtube() -> str | None:
    from google.auth.exceptions import RefreshError
    from youtube_uploader import get_credentials, token_path
    if not token_path("투게독").exists():  # 실패가 출입증 준비 전에 난 경우 — 확인 불가, 매일 점검에 맡김
        return None
    try:
        get_credentials("투게독")
    except (RefreshError, SystemExit) as e:
        return ("🔑 **유튜브 로그인이 풀렸어요** — 이대로면 업로드가 전부 멈춰요. (사람만 할 수 있는 일)\n"
                f"원인: 구글 비밀번호 변경·보안 점검 등 ({str(e)[:80]})\n" + ASK +
                " PC에서 브라우저 로그인 1번(채널 투게독 선택)이면 끝나요.")
    except Exception as e:
        return f"⚠️ 유튜브 로그인 확인 중 오류: {str(e)[:150]}"
    return None


def check_gemini_text() -> str | None:
    from google import genai
    from script_writer import FALLBACK_MODELS, HTTP, _generate, types
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=HTTP)
        cfg = types.GenerateContentConfig(response_mime_type="application/json")
        _generate(client, FALLBACK_MODELS, '{"ok": 1} 그대로 JSON으로 답해', cfg)
    except BaseException as e:  # _generate는 SystemExit로 실패를 알림
        return f"✍️ **대본 AI(Gemini)가 응답하지 않아요** — 대체 모델까지 실패. ({str(e)[:150]})\n" + ASK
    return None


def check_gemini_tts() -> str | None:
    from voice_gemini import synth
    try:
        with tempfile.TemporaryDirectory() as d:
            synth("점검 중입니다.", "Kore", "", Path(d) / "t.wav")
    except Exception as e:
        return (f"🎙 Gemini 목소리가 안 돼요 — 그 편은 기계음(edge-tts)으로 나가요. "
                f"업로드는 계속돼요. ({str(e)[:150]})\n" + ASK)
    return None


def check_pixabay() -> str | None:
    import requests
    try:
        r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                         params={"key": os.environ["PIXABAY_API_KEY"], "q": "dog", "per_page": 3})
        r.raise_for_status()
        if not r.json().get("hits"):
            return "🎞 배경 영상(Pixabay) 검색 결과가 비어 있어요.\n" + ASK
    except Exception as e:
        return f"🎞 **배경 영상(Pixabay) 연결 실패** — 영상 제작이 멈출 수 있어요. ({str(e)[:150]})\n" + ASK
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--after-failure", action="store_true")
    p.add_argument("--weekly", action="store_true")
    a = p.parse_args()
    load_env()

    problems = []
    # 예비 실행(최대 3번)이 다 채워줄 시간이 남았으면 조용히. 12시간 안 회차가 비었으면 알림
    gaps = empty_slots(12)
    if gaps:
        when = ", ".join(t.strftime("%m/%d %H:%M") for t in gaps)
        problems.append(f"📭 **곧 공개될 쇼츠가 비어 있어요**: {when}\n자동 예비 실행이 다시 시도하지만, "
                        f"계속 실패 중일 수 있어요. 실행 기록: {REPO}/actions\n" + ASK)
    checks = [check_youtube] if a.after_failure else [check_youtube, check_gemini_text, check_gemini_tts, check_pixabay]
    for c in checks:
        msg = c()
        print(f"{c.__name__}: {'OK' if not msg else msg}")
        if msg:
            problems.append(msg)

    if problems:
        alert.send("🚨 **투게독 쇼츠 점검 — 확인이 필요해요**\n\n" + "\n\n".join(problems))
    elif a.weekly:
        week = [h for h in load_history() if h.get("video_id") and h.get("date", "") >=
                (datetime.now(KST) - timedelta(days=7)).isoformat()]
        alert.send(f"✅ **투게독 쇼츠 주간 보고** — 모두 정상이에요\n지난 7일 {len(week)}편 제작·예약 완료\n"
                   + (note + "\n" if note else "") +
                   f"매일 자동 점검 중이고, 문제 있을 때만 따로 연락드릴게요. 📊 {STUDIO}")
    print("문제", len(problems), "건")
    sys.exit(0)


if __name__ == "__main__":
    main()
