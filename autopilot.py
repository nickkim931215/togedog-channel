"""투게독 채널 완전 자동: 주제 선정 → 대본 → 영상 → 예약 업로드 → 기록. (GitHub Actions)
캄모니(youtube-auto) 코드를 복사해 고친 것 — 캄모니와 저장소 · 비밀값 · 시각 모두 따로.

  python autopilot.py --schedule        # 예약 대기열 채우기 (06:30 / 18:00 / 21:00 KST) — 자동 실행은 이것
  python autopilot.py --plan            # 만들 회차 수만 출력
  python autopilot.py --no-upload       # 업로드 없이 1편 시험 제작
  python autopilot.py --category behavior --topic "..."   # 카테고리 · 주제 지정
"""
import argparse
import json
import os
import random
import re
import traceback
from datetime import datetime, timedelta, timezone

from settings import BASE

KST = timezone(timedelta(hours=9))
CHANNEL = "투게독"
HISTORY = BASE / "history.json"
APP_URL = "https://togedog.app"
PROFILE = ("투게독(TogeDog) — 강아지와 보호자를 위한 쇼츠 채널. 견종 이야기, 산책 꿀팁, 강아지 행동의 속뜻, "
           "귀엽고 웃긴 강아지 순간을 다룸. 말투는 다정하고 친근한 존댓말. "
           "수의학적 진단·치료를 단정하지 않고, 위험한 음식 등 안전 정보는 정확하게. 정치·종교·자극적인 내용 금지.")

# 카테고리: (설명, 대본 지시, 목소리 톤, 태그)
CATEGORIES = {
    "breed": ("견종 상식 — 한 견종의 성격 · 생김새의 이유 · 키울 때 알아둘 점 · 의외의 사실",
              "첫 문장은 '이 견종, 사실 이랬다고요?' 같은 궁금증 유발, 중간은 그 견종만의 특징 2~3개, "
              "마지막은 '우리 집에도 이 친구 있다면 댓글로!'. 널리 알려진 사실만.",
              "신기한 걸 알려주듯 밝고 다정하게",
              ["견종", "강아지상식", "반려견"]),
    "walk_tips": ("산책 꿀팁 — 계절 · 날씨 · 산책 매너 · 장비 · 강아지가 더 좋아하는 산책법",
                  "첫 문장은 '산책할 때 이거 하고 계셨나요?' 같은 공감, 중간은 바로 써먹는 팁 2~3개, "
                  "마지막은 '오늘 산책에서 해 보세요'. 안전 정보는 정확하게.",
                  "산책 나가기 전처럼 상쾌하고 경쾌하게",
                  ["강아지산책", "산책꿀팁", "반려견"]),
    "behavior": ("행동의 속뜻 — 강아지가 이런 행동을 하는 진짜 이유 (꼬리 · 귀 · 하품 · 빙글빙글 · 엉덩이 붙이기 등)",
                 "첫 문장은 '우리 강아지가 이러는 이유, 알고 계셨나요?', 중간은 행동 하나와 그 이유 2~3가지, "
                 "마지막은 '우리 아이는 언제 이러는지 댓글로!'. 진단처럼 단정하지 말 것.",
                 "다정하고 사랑스럽게, 미소를 머금고",
                 ["강아지행동", "강아지심리", "반려견"]),
    "cute": ("귀엽고 웃긴 순간 — 강아지만 할 수 있는 사랑스러운 행동 · 보호자만 아는 공감 포인트",
             "'강아지 키우는 사람만 아는' 공감으로 시작, 귀여운 순간 3~4개를 짧고 리듬감 있게, "
             "마지막은 '공감되면 좋아요!'. 짧고 웃음 나게.",
             "웃음을 참으며 이야기하듯 장난스럽고 사랑스럽게",
             ["강아지", "귀여운강아지", "댕댕이"]),
}
SLOTS = {
    "morning": ("아침 6시 반. 일어나서 아침 산책 나가기 전의 보호자들", {"walk_tips": 3, "behavior": 2, "breed": 2, "cute": 2}),
    "evening": ("저녁 6시. 퇴근길 · 저녁 산책 전에 가볍게 보는 사람들", {"cute": 3, "walk_tips": 2, "breed": 2, "behavior": 2}),
    "night": ("밤 9시. 저녁 산책 뒤 강아지와 쉬는 보호자들", {"behavior": 3, "cute": 3, "breed": 2, "walk_tips": 1}),
}
LABELS = {"breed": "견종 상식", "walk_tips": "산책 꿀팁", "behavior": "행동의 속뜻", "cute": "댕댕이 공감"}
BASE_TAGS = ["Shorts", "투게독", "강아지"]
# 유튜브 예약 공개 시각 (KST) — 사장님 11/09 결정 06:30 / 18:00 / 21:00
PUBLISH_TIMES = {"morning": (6, 30), "evening": (18, 0), "night": (21, 0)}
# 18시 칸은 이틀에 한 번 투게독 플레이 영상 (사장님 11/09 결정). 플레이 영상 제작이 준비되기 전엔 강아지 영상으로 채움
PLAY_READY = False
HORIZON = timedelta(hours=26)    # 이 시간 안에 공개될 회차만 미리 만듦
LEAD = timedelta(minutes=25)     # 1편 제작·업로드 여유
SCENE_RULE = ("\n배경 영상 검색어(scenes)는 모두 강아지 장면으로: 첫 단어는 반드시 dog "
              "(예: \"dog running park\", \"dog tilting head indoor\", \"dog walking leash street\", \"dog sleeping sofa\"). "
              "특정 견종이면 두 번째 단어에 견종 영어 이름 (예: \"dog corgi grass\"). 고양이 · 사람 얼굴 클로즈업 금지.")


def is_play_slot(slot: str, at: datetime) -> bool:
    return slot == "evening" and at.date().toordinal() % 2 == 0


def pick_category(slot: str, history: list[dict]) -> str:
    """시간대 비중대로 랜덤, 단 직전 영상과 같은 카테고리는 피함."""
    weights = dict(SLOTS[slot][1])
    last = history[-1].get("category") if history else None
    if last in weights and len(weights) > 1:
        weights.pop(last)
    return random.choices(list(weights), weights=list(weights.values()))[0]


def current_slot(now: datetime) -> str:
    return "morning" if 4 <= now.hour < 12 else "evening" if 12 <= now.hour < 19 else "night"


def load_history() -> list[dict]:
    return json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []


def pending_slots(now: datetime, history: list[dict]) -> list[tuple[str, datetime]]:
    """앞으로 HORIZON 안에 공개될 회차 중 아직 예약 업로드 안 된 것 (공개 시각 순)."""
    done = {h["publish_at"] for h in history if h.get("publish_at") and h.get("video_id")}
    todo = []
    for day in range(3):
        d = (now + timedelta(days=day)).date()
        for slot, (hh, mm) in PUBLISH_TIMES.items():
            at = datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)
            if at - now <= HORIZON and at.isoformat() not in done:
                todo.append((slot, at))
    todo.sort(key=lambda x: x[1])
    return [(slot, at) for i, (slot, at) in enumerate(todo) if at - now > LEAD * (i + 1)]


def run_once(slot: str, topic: str | None, upload: bool, privacy: str, category: str | None = None,
             publish_at: datetime | None = None) -> dict:
    from script_writer import pick_topic, write_script
    from shorts_maker import make_shorts

    history = load_history()
    if publish_at and is_play_slot(slot, publish_at) and PLAY_READY:
        return run_play(slot, upload, privacy, publish_at, history)
    category = category or pick_category(slot, history)
    cat_desc, guide, tone, cat_tags = CATEGORIES[category]
    if not topic:
        topic = pick_topic(PROFILE, f"{SLOTS[slot][0]}\n이번 카테고리: {cat_desc}", [h["topic"] for h in history])
    print(f"▶ [{slot}/{category}] 주제: {topic}")
    s = write_script(f"{topic}\n(채널 콘셉트: {PROFILE})\n(카테고리 지침: {guide}){SCENE_RULE}", seconds=30)
    title, sentences = s["title"], s["sentences"]
    print(f"  제목: {title}\n" + "\n".join(f"  - {x}" for x in sentences) + "\n  장면: " + " / ".join(s.get("scenes") or []))

    now = datetime.now(KST)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40]
    out = BASE / "output" / f"{now:%Y%m%d_%H%M}_{slot}_{safe}.mp4"
    out.parent.mkdir(exist_ok=True)
    make_shorts("\n".join(sentences), out, title=title, scenes=s.get("scenes"), tone=tone,
                label=f"TOGEDOG · {LABELS[category]}")

    record = {"date": now.isoformat(timespec="minutes"), "slot": slot, "category": category, "topic": topic,
              "title": title, "file": out.name, "video_id": None}
    if publish_at:
        record["publish_at"] = publish_at.isoformat()
    return finish(record, s, out, upload, privacy, publish_at, history, cat_tags)


def run_play(slot: str, upload: bool, privacy: str, publish_at: datetime | None, history: list[dict]) -> dict:
    """🎮 투게독 플레이 영상 (play_maker: 게임 촬영 모드를 로봇이 찍음 — 매번 사람 수 · 장면 · 낮/저녁이 다름)."""
    from play_maker import make_play
    now = datetime.now(KST)
    out = BASE / "output" / f"{now:%Y%m%d_%H%M}_{slot}_play.mp4"
    out.parent.mkdir(exist_ok=True)
    s = make_play(out)
    print(f"▶ [{slot}/play] 제목: {s['title']}")
    record = {"date": now.isoformat(timespec="minutes"), "slot": slot, "category": "play", "topic": "투게독 플레이",
              "title": s["title"], "file": out.name, "video_id": None,
              "play": {k: s[k] for k in ("seed", "cast", "time", "scenes")}}
    if publish_at:
        record["publish_at"] = publish_at.isoformat()
    return finish(record, s, out, upload, privacy, publish_at, history, ["투게독플레이", "강아지게임"])


def finish(record: dict, s: dict, out, upload: bool, privacy: str, publish_at, history: list[dict],
           cat_tags: list[str]) -> dict:
    """업로드(선택) + history 기록."""
    from youtube_uploader import upload_video
    if upload:
        tags = [t.replace(" ", "") for t in s.get("tags", [])][:8]
        hashtags = list(dict.fromkeys(["Shorts", "투게독"] + cat_tags[:2] + tags[:3]))
        desc = (f"{s.get('description', '')}\n\n" + "\n".join(s["sentences"])
                + f"\n\n🐶 강아지와 함께 걷는 산책 게임, 투게독 → {APP_URL}\n\n" + " ".join("#" + t for t in hashtags))
        record["video_id"] = upload_video(out, f"{s['title']} #Shorts", CHANNEL, description=desc,
                                          tags=list(dict.fromkeys(BASE_TAGS + cat_tags + tags)), privacy=privacy,
                                          publish_at=publish_at)
    history.append(record)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def run_with_retry(**kw) -> dict | None:
    """실패 시 새 주제로 1번 더 시도 (Gemini 과부하·영상 검색 실패 등 일시적 오류 대비)."""
    for attempt in (1, 2):
        try:
            rec = run_once(**kw)
            print(f"✅ 완료: {rec}")
            return rec
        except SystemExit as e:
            if "토큰" in str(e) or "채널" in str(e):  # 로그인 문제는 재시도해도 소용없음
                raise
            print(f"⚠ {e}")
        except Exception:
            traceback.print_exc()
        print("⚠ 실패 → 재시도" if attempt == 1 else "❌ 2회 모두 실패")
    return None


def schedule():
    """예약 대기열 채우기. 하나가 실패해도 다음 회차는 계속 만들고, 실패가 있으면 마지막에 실패 처리."""
    todo = pending_slots(datetime.now(KST), load_history())
    if not todo:
        print("예약할 회차 없음 (이미 다 채워짐)")
        return
    print("만들 회차: " + ", ".join(f"{at:%m/%d %H:%M}({slot})" for slot, at in todo))
    failed = []
    for slot, at in todo:
        if at - datetime.now(KST) <= LEAD / 2:
            print(f"⏭ {at:%m/%d %H:%M} 공개 시각이 너무 가까워 건너뜀")
            failed.append(at)
            continue
        print(f"\n===== {at:%m/%d %H:%M} 공개 예약분 ({slot}) =====")
        if not run_with_retry(slot=slot, topic=None, upload=True, privacy="private", publish_at=at):
            failed.append(at)
        os.environ.pop("VOICE", None)  # 목소리 고정은 첫 편에만
    if failed:
        raise SystemExit("❌ 실패한 회차: " + ", ".join(f"{at:%m/%d %H:%M}" for at in failed))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schedule", action="store_true", help="예약 대기열 채우기")
    p.add_argument("--plan", action="store_true", help="만들 회차 수만 출력")
    p.add_argument("--slot", choices=list(SLOTS))
    p.add_argument("--topic")
    p.add_argument("--category", choices=list(CATEGORIES))
    p.add_argument("--privacy", default="public", choices=["private", "unlisted", "public"])
    p.add_argument("--no-upload", action="store_true")
    a = p.parse_args()
    if a.plan:
        todo = pending_slots(datetime.now(KST), load_history())
        print(len(todo), " ".join(f"{at:%m/%d_%H:%M}" for _, at in todo))
        return
    if a.schedule:
        return schedule()
    slot = a.slot or current_slot(datetime.now(KST))
    if not run_with_retry(slot=slot, topic=a.topic, upload=not a.no_upload, privacy=a.privacy, category=a.category):
        raise SystemExit("❌ 2회 모두 실패")


if __name__ == "__main__":
    main()
