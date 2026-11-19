"""투게독 AI 일터(관리자 페이지 togedog.app/admin/office) 실시간 업무 보고 — 진행 상황을 비밀 Gist의 office.json에 기록.
캄모니 office.py를 가져와 고친 것 (디스코드 아이디어 대기열은 투게독에 없음).

OFFICE_GIST·OFFICE_TOKEN 환경변수가 없거나 기록에 실패해도 본 작업엔 영향 없음 (표준 라이브러리만 사용 —
패키지 설치 전 단계에서도 호출됨. 어떤 오류도 밖으로 내보내지 않음 — 업로드가 최우선).
  python office.py start                      # 출근 (새 실행 시작)
  python office.py plan "1 11/20_12:30"       # 예약 대기열 확인 결과 (autopilot --plan 출력)
  python office.py say <부서> <직원> <메시지>  # 아무 보고나
  python office.py finish success|failure     # 퇴근 (채널 통계도 갱신)
  python office.py stats                      # 채널 통계만 갱신
부서: ops 서버실 / play 게임 촬영실 / plan·script 작가실 / voice 녹음실 / footage 자료실 / edit 편집실 / publish 업로드실
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HISTORY = Path(__file__).resolve().parent / "history.json"
MAX_LOG = 150
LABELS = {"breed": "견종 상식", "walk_tips": "산책 꿀팁", "behavior": "행동의 속뜻", "cute": "댕댕이 공감",
          "play": "게임 플레이"}
_state = None


def _enabled() -> bool:
    return bool(os.environ.get("OFFICE_GIST") and os.environ.get("OFFICE_TOKEN"))


def _api(method: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/gists/" + os.environ["OFFICE_GIST"], method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": "Bearer " + os.environ["OFFICE_TOKEN"], "User-Agent": "togedog-office",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _load() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(_api("GET")["files"]["office.json"]["content"])
        except Exception:
            _state = {}
    return _state


def _schedule_board() -> dict:
    """history.json → 앞으로 공개될 예약분 + 최근 완료분 (사무실 게시판용)."""
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = datetime.now(KST).isoformat()
    item = lambda h: {"publish_at": h.get("publish_at") or h.get("date"), "category": h.get("category"),
                      "label": LABELS.get(h.get("category"), ""), "title": h.get("title"),
                      "voice": h.get("voice"), "video_id": h.get("video_id")}
    done = [h for h in hist if h.get("video_id")]
    upcoming = [item(h) for h in done if (h.get("publish_at") or "") > now]
    published = [item(h) for h in done if (h.get("publish_at") or h.get("date") or "") <= now][-8:]
    return {"upcoming": sorted(upcoming, key=lambda x: x["publish_at"]), "published": published[::-1],
            "total": len(done)}


def _yt_token() -> str:
    """YT_TOKEN 환경변수(워크플로 secret) 또는 token_togedog.json의 refresh_token으로 접근 토큰 발급."""
    raw = os.environ.get("YT_TOKEN") or (HISTORY.parent / "token_togedog.json").read_text(encoding="utf-8")
    t = json.loads(raw.lstrip("﻿"))
    body = urllib.parse.urlencode({"client_id": t["client_id"], "client_secret": t["client_secret"],
                                   "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen(t.get("token_uri", "https://oauth2.googleapis.com/token"), body, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def _yt(path: str, token: str, **q) -> dict:
    req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/{path}?" + urllib.parse.urlencode(q),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _channel_stats():
    """채널 구독자·조회수 + 공개된 영상별 조회수 (유튜브 API 2유닛). 날짜별 기록은 stats_hist에 쌓음."""
    try:
        token = _yt_token()
        ch = _yt("channels", token, part="statistics", mine="true")["items"][0]["statistics"]
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
        now = datetime.now(KST).isoformat()
        titles = {h["video_id"]: h.get("title", "") for h in hist
                  if h.get("video_id") and (h.get("publish_at") or h.get("date") or "") <= now}
        vids = []
        ids = list(titles)[-50:]
        if ids:
            for v in _yt("videos", token, part="statistics", id=",".join(ids)).get("items", []):
                s = v.get("statistics", {})
                vids.append({"id": v["id"], "title": titles.get(v["id"], ""), "views": int(s.get("viewCount", 0)),
                             "likes": int(s.get("likeCount", 0))})
    except Exception as e:
        print(f"  (채널 통계 실패: {str(e)[:120]})")
        return
    st = _load()
    subs, views = int(ch.get("subscriberCount", 0)), int(ch.get("viewCount", 0))
    st["stats"] = {"at": _now(), "subs": subs, "views": views, "videos": int(ch.get("videoCount", 0)),
                   "hidden": bool(ch.get("hiddenSubscriberCount")),
                   "top": sorted(vids, key=lambda v: -v["views"])[:3],
                   "shorts_views": sum(v["views"] for v in vids), "shorts_likes": sum(v["likes"] for v in vids)}
    day = datetime.now(KST).strftime("%Y-%m-%d")
    sh = [x for x in st.get("stats_hist", []) if x.get("d") != day]
    st["stats_hist"] = (sh + [{"d": day, "subs": subs, "views": views}])[-90:]


def _save():
    if not _enabled():
        return
    try:
        _state["updated"] = _now()
        _api("PATCH", {"files": {"office.json": {"content": json.dumps(_state, ensure_ascii=False)}}})
    except Exception as e:
        print(f"  (사무실 보고 실패: {str(e)[:120]})")


def report(dept: str, msg: str, who: str = "", stage: str | None = None, kind: str = "work", **current):
    """업무 보고 한 줄. stage를 주면 지금 일하는 부서가 바뀜, current는 진행 중인 영상 정보 갱신."""
    if not _enabled():
        return
    try:
        st = _load()
        if stage:
            st["stage"] = stage
        if current:
            st.setdefault("current", {}).update(current)
        st.setdefault("log", []).append({"t": _now(), "dept": dept, "who": who, "msg": msg, "kind": kind,
                                         "stage": st.get("stage", "idle")})
        st["log"] = st["log"][-MAX_LOG:]
        _save()
    except Exception as e:
        print(f"  (사무실 보고 실패: {str(e)[:120]})")


def current_dept() -> str:
    """지금 일하는 부서 (문제 보고용)."""
    try:
        stage = _load().get("stage") if _enabled() else None
    except Exception:
        stage = None
    return stage if stage in ("play", "plan", "script", "voice", "footage", "edit", "publish") else "ops"


def start():
    if not _enabled():
        return
    st = _load()
    rid = os.environ.get("GITHUB_RUN_ID", "")
    st["run"] = {"id": rid, "event": os.environ.get("GITHUB_EVENT_NAME", "local"), "started": _now(),
                 "status": "working",
                 "url": f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{rid}"}
    st["current"] = {}
    st["board"] = _schedule_board()
    report("ops", "출근했습니다! 오늘 할 일 확인할게요 🐾", "actions", stage="setup", kind="start")


def finish(status: str):
    if not _enabled():
        return
    st = _load()
    ok = status == "success"
    st.setdefault("run", {})["status"] = "done" if ok else "failed"
    st["run"]["ended"] = _now()
    st["board"] = _schedule_board()
    st["current"] = {}
    _channel_stats()
    report("ops", "오늘 업무 끝! 모두 수고했어요 🦴" if ok else "문제가 있었어요. 대표님 디스코드로 알림 갔습니다 💦",
           "pd", stage="idle", kind="end" if ok else "error")


def board():
    """예약 게시판만 갱신 (업로드 직후, 다음 보고 때 같이 저장)."""
    try:
        if _enabled():
            _load()["board"] = _schedule_board()
    except Exception:
        pass


def main():
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "start":
        start()
    elif cmd == "finish":
        finish(args[0] if args else "success")
    elif cmd == "plan":
        n, *slots = (args[0] if args else "0").split()
        if n == "0":
            report("ops", "예약이 이미 다 차 있어요. 오늘은 낮잠 타임 💤", "pd")
        elif n == "manual":
            report("ops", "대표님 직접 지시! 지금 바로 1편 만듭니다", "pd")
        else:
            report("ops", f"만들 회차 {n}개: " + ", ".join(s.replace("_", " ") for s in slots), "pd",
                   todo=[s.replace("_", " ") for s in slots])
    elif cmd == "stats" and _enabled():
        _load()
        _channel_stats()
        _save()
    elif cmd == "say" and len(args) >= 3:
        report(args[0], " ".join(args[2:]), args[1])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 일터 보고는 덤 — 절대 실행을 멈추지 않음
        print(f"  (사무실 보고 실패: {str(e)[:120]})")
