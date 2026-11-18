"""무료 스톡 영상 검색/다운로드 (Pixabay / Pexels, 로컬 캐시).

.env에 PIXABAY_API_KEY 또는 PEXELS_API_KEY 중 있는 것을 사용 (둘 다 있으면 Pexels 먼저).
- 최근 영상들에 쓴 클립은 다시 안 씀 (history.json의 clips)
- 문장(context)을 주면 Gemini가 후보 미리보기 사진을 보고 내용에 맞는 영상을 고름
"""
import json
import os
import random

import requests

from settings import BASE, load_env

CACHE = BASE / "cache"
HISTORY = BASE / "history.json"
RECENT_VIDEOS = 90  # 최근 이만큼의 영상(약 한 달)에 쓴 클립은 제외
PICKED: list[str] = []  # 이번 영상에서 고른 클립 → autopilot이 history에 기록 (take_picked)


def take_picked() -> list[str]:
    """이번 영상에 쓴 클립 id 목록을 꺼내고 비움."""
    out = list(dict.fromkeys(PICKED))
    PICKED.clear()
    return out


def _recent_ids() -> set[str]:
    try:
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    return {c for h in history[-RECENT_VIDEOS:] for c in h.get("clips", [])} | set(PICKED)


def _download(url: str, path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=90, headers={"User-Agent": "Mozilla/5.0"}) as d:
            d.raise_for_status()
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as out:
                for chunk in d.iter_content(1 << 20):
                    out.write(chunk)
            tmp.replace(path)
    return str(path)


# ---------- Pexels ----------
def _pexels(query, used, min_sec, key):
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=20,
                     params={"query": query, "orientation": "portrait", "per_page": 15})
    r.raise_for_status()
    recent = _recent_ids()
    videos = [v for v in r.json().get("videos", [])
              if ("pexels", v["id"]) not in used and f"pexels:{v['id']}" not in recent]
    candidates = ([v for v in videos if v.get("duration", 0) >= min_sec] or videos)[:6]
    random.shuffle(candidates)
    for v in candidates:
        files = [f for f in v.get("video_files", []) if f.get("height") and f.get("width")
                 and f["height"] > f["width"] and f.get("file_type") == "video/mp4"]
        if not files:
            continue
        good = [f for f in files if f["height"] >= 1920]
        f = min(good, key=lambda x: x["height"]) if good else max(files, key=lambda x: x["height"])
        used.add(("pexels", v["id"]))
        PICKED.append(f"pexels:{v['id']}")
        return _download(f["link"], CACHE / "pexels" / f"{v['id']}_{f['height']}.mp4")
    return None


# ---------- Pixabay ----------
# 검색어 첫 단어 = 장면의 주인공. 태그에 이게 없으면 버림 (예: "cute puppy tilting head" → 다람쥐 'cute, head' 배제)
SYNONYMS = {"puppy": "dog", "puppies": "dog", "doggy": "dog", "kitten": "cat", "kitty": "cat", "kittens": "cat"}
SKIP_TAGS = {"cartoon", "animation", "illustration", "render", "3d"}
ADJECTIVES = {"cute", "happy", "little", "small", "big", "beautiful", "young", "old", "calm", "cozy", "bright", "golden"}
JUDGE_MAX = 8  # Gemini에게 한 번에 보여 줄 후보 수
JUDGE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"]


def _norm(w: str) -> str:
    w = w.lower().strip()
    w = SYNONYMS.get(w, w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _tag_words(tags: str) -> set[str]:
    return {_norm(w) for t in tags.split(",") for w in t.split()}


def _pixabay_cands(query, used, min_sec, key, strict=True) -> list[tuple]:
    """관련도 순으로 정렬한 후보 [(점수, 세로?, 충분히 김?, hit, 파일)]. 최근 영상에 쓴 클립은 제외."""
    r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                     params={"key": key, "q": query[:100], "per_page": 100, "safesearch": "true", "order": "popular",
                             "video_type": "film"})  # 실사만 (일러스트/애니메이션 제외)
    r.raise_for_status()
    recent = _recent_ids()
    hits = [h for h in r.json().get("hits", [])
            if ("pixabay", h["id"]) not in used and f"pixabay:{h['id']}" not in recent]

    def best(h):
        """세로 영상 우선, 그다음 해상도 높은 것 (가로 영상은 가운데를 세로로 잘라 씀)."""
        vids = [v for v in h["videos"].values() if v.get("url")]
        if not vids:
            return None
        portrait = [v for v in vids if v["height"] > v["width"]]
        pool = portrait or vids
        # 1080p 이상 중 가장 작은 것 (4K는 다운로드가 커서 1080p 우선)
        ok = [v for v in pool if min(v["width"], v["height"]) >= 1080]
        return min(ok, key=lambda v: v["width"]) if ok else max(pool, key=lambda v: v["width"])

    qwords = [_norm(w) for w in query.split() if len(w) > 2]
    subject = next((w for w in qwords if w not in ADJECTIVES), "")
    scored = []
    for h in hits:
        v = best(h)
        tags = _tag_words(h.get("tags", ""))
        if not v or tags & SKIP_TAGS or (strict and subject and subject not in tags):
            continue
        # 태그가 검색어와 많이 겹칠수록 관련도 높음 (주인공 단어는 2점)
        match = sum(w in tags for w in qwords) + (subject in tags)
        scored.append((match, v["height"] > v["width"], h.get("duration", 0) >= min_sec, h, v))
    # 관련도 → 세로 → 충분히 긴 것 순
    scored.sort(key=lambda x: x[:3], reverse=True)
    return scored


def _thumb(h) -> bytes | None:
    for size in ("small", "medium", "tiny", "large"):
        url = h["videos"].get(size, {}).get("thumbnail")
        if not url:
            continue
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            continue
    return None


JUDGE_PROMPT = """유튜브 쇼츠 배경 영상을 고르는 중이야.
{context}
검색어: {query}

아래 사진 {n}장은 후보 영상의 한 장면이야 (순서대로 1번~{n}번).
이 문장이 나오는 동안 뒤에 깔렸을 때 가장 잘 어울리는 영상 번호를 골라.
- 문장이 말하는 대상(음식·동물·물건·장소·행동)이 실제로 화면에 보여야 함. 예: 돈가스 이야기인데 꽃이나 깃발이면 탈락
- 강아지·고양이 이야기면 그 동물이 화면에 크게 잘 보여야 함 (멀리 작게 보이면 감점), 품종이 나오면 그 품종에 가까울수록 좋음
- 큰 글자·로고·워터마크가 있거나, 너무 어둡거나, 흐릿하면 감점
- 문장의 핵심 대상 하나만 분명히 보이면 충분함 (문장 내용을 전부 담을 필요는 없음)
- 어울리는 게 하나도 없으면 0
JSON으로만 답해: {{"pick": 번호, "alt": "0일 때 다시 찾아볼 영어 검색어 2~3단어 — 지금 검색어보다 더 넓고 흔한 장면 (예: 돈가스→fried food plate, 양배추 보관→vegetables kitchen counter), 주인공 명사로 시작"}}"""


def _judge(pool: list, query: str, context: str) -> tuple[int | None, str]:
    """Gemini가 미리보기 사진으로 고르기 → (pool 인덱스 / -1 = 다 안 맞음 / None = 판단 실패, 대체 검색어)."""
    try:
        from google import genai
        from google.genai import types

        from script_writer import HTTP, _generate

        shown, parts = [], []
        for i, c in enumerate(pool):
            img = _thumb(c[3])
            if img:
                shown.append(i)
                parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
        if not shown:
            return None, ""
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=HTTP)
        resp = _generate(client, JUDGE_MODELS, [JUDGE_PROMPT.format(context=context, query=query, n=len(shown)), *parts],
                         types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2))
        data = json.loads(resp.text)
        pick, alt = int(data.get("pick") or 0), str(data.get("alt") or "").strip()
        return (shown[pick - 1] if 1 <= pick <= len(shown) else -1), alt
    except (Exception, SystemExit) as e:  # 판단이 실패해도 영상 제작은 계속 (예전 방식으로)
        print(f"    (배경 판단 건너뜀: {str(e)[:100]})")
        return None, ""


def _take(cand, used) -> str:
    *_, h, v = cand
    used.add(("pixabay", h["id"]))
    PICKED.append(f"pixabay:{h['id']}")
    return _download(v["url"], CACHE / "pixabay" / f"{h['id']}_{v['width']}x{v['height']}.mp4")


def _pixabay_pick(tries: list[tuple[str, bool]], used, min_sec, key, context: str) -> str | None:
    """검색어를 차례로 넓혀 가며 후보를 모으고, 문장에 맞는 영상을 골라 다운로드."""
    judging = bool(context and os.environ.get("GEMINI_API_KEY"))
    queue, tried, first, alts = list(tries), set(), None, 0
    while queue:
        q, strict = queue.pop(0)
        if (q, strict) in tried:
            continue
        tried.add((q, strict))
        scored = _pixabay_cands(q, used, min_sec, key, strict)
        if not scored:
            continue
        if not judging:
            # 판단 없이: 관련도 최고점 후보 몇 개 중 랜덤 (매번 같은 영상 안 나오게)
            return _take(random.choice([x for x in scored if x[0] == scored[0][0]][:6]), used)
        # 관련도 최고점 -1 까지 넓게 모아 섞은 뒤 Gemini가 고름
        pool = [x for x in scored if x[0] >= scored[0][0] - 1][:JUDGE_MAX * 3]
        random.shuffle(pool)
        pool = pool[:JUDGE_MAX]
        idx, alt = _judge(pool, q, context)
        if idx is None:
            return _take(pool[0], used)
        if idx >= 0:
            print(f"    ✔ '{q}' 후보 {len(pool)}개 중 내용에 맞는 영상 선택")
            return _take(pool[idx], used)
        print(f"    ✘ '{q}' 후보 {len(pool)}개 모두 내용과 안 맞음" + (f" → '{alt}' 로 다시" if alt and alts < 2 else ""))
        first = first or pool[0]
        if alt and alts < 2:  # Gemini가 제안한 더 넓은 검색어를 바로 다음에 (최대 2번)
            queue.insert(0, (alt, True))
            alts += 1
    # 끝까지 맞는 게 없으면 처음 후보 (그라데이션 배경보다는 나음)
    return _take(first, used) if first else None


def fetch_clip(query: str, used: set, min_sec: float = 0, context: str = "") -> str | None:
    """검색어에 맞는 영상 1개를 받아 로컬 경로 반환 (없으면 None → 그라데이션 배경).
    context = 영상 제목 + 이 장면의 문장 — 있으면 Gemini가 내용에 맞는 영상을 고름."""
    load_env()
    words = query.split()
    # 주인공 단어가 태그에 있는 영상만 → 검색어 줄여서(주인공+1단어, 주인공만) → 그래도 없으면 제한 없이
    words = [w for w in words if w.lower() not in ADJECTIVES] or words
    tries = [(q, True) for q in dict.fromkeys([query, " ".join(words[:2]), " ".join(words[:1])])] + [(query, False)]
    if os.environ.get("PEXELS_API_KEY"):
        for q, _ in tries[:-1]:
            try:
                path = _pexels(q, used, min_sec, os.environ["PEXELS_API_KEY"])
            except requests.RequestException as e:
                print(f"    pexels 검색 실패({q}): {e}")
                break
            if path:
                return path
    if os.environ.get("PIXABAY_API_KEY"):
        try:
            return _pixabay_pick(tries, used, min_sec, os.environ["PIXABAY_API_KEY"], context)
        except requests.RequestException as e:
            print(f"    pixabay 검색 실패({query}): {e}")
    return None
