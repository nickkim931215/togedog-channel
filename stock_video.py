"""무료 스톡 영상 검색/다운로드 (Pixabay / Pexels, 로컬 캐시).

.env에 PIXABAY_API_KEY 또는 PEXELS_API_KEY 중 있는 것을 사용 (둘 다 있으면 Pexels 먼저).
- 최근 영상들에 쓴 클립은 다시 안 씀 (history.json의 clips)
- 문장(context)을 주면 Gemini가 후보 미리보기 사진을 보고 내용에 맞는 영상을 고름
- Pixabay는 영어 검색어에 다른 영어 표현 · 다른 언어(한국어·일본어·독일어 등) 검색어 후보를 섞어서 고름 (비슷한 영상 반복 줄이기)
"""
import json
import os
import random
from pathlib import Path

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
JUDGE_ROUNDS = 4  # 장면 하나에 판단 최대 횟수 (시간·무료 한도 보호)
JUDGE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"]


def _norm(w: str) -> str:
    w = w.lower().strip()
    w = SYNONYMS.get(w, w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _tag_words(tags: str) -> set[str]:
    return {_norm(w) for t in tags.split(",") for w in t.split()}


def _best_file(h):
    """세로 영상 우선, 그다음 해상도 높은 것 (가로 영상은 가운데를 세로로 잘라 씀)."""
    vids = [v for v in h["videos"].values() if v.get("url")]
    if not vids:
        return None
    portrait = [v for v in vids if v["height"] > v["width"]]
    pool = portrait or vids
    # 1080p 이상 중 가장 작은 것 (4K는 다운로드가 커서 1080p 우선)
    ok = [v for v in pool if min(v["width"], v["height"]) >= 1080]
    return min(ok, key=lambda v: v["width"]) if ok else max(pool, key=lambda v: v["width"])


def _pixabay_cands(query, used, min_sec, key, strict=True, lang="en", subjects=()) -> list[tuple]:
    """관련도 순으로 정렬한 후보 [(점수, 세로?, 충분히 김?, hit, 파일)]. 최근 영상에 쓴 클립은 제외.
    lang = Pixabay 검색 언어(태그도 그 언어로 옴), subjects = 그 언어의 주인공 단어들(태그에 하나는 있어야 함)."""
    r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                     params={"key": key, "q": query[:100], "lang": lang, "per_page": 100, "safesearch": "true",
                             "order": "popular", "video_type": "film"})  # 실사만 (일러스트/애니메이션 제외)
    r.raise_for_status()
    recent = _recent_ids()
    hits = [h for h in r.json().get("hits", [])
            if ("pixabay", h["id"]) not in used and f"pixabay:{h['id']}" not in recent]

    if lang == "en":
        qwords = [_norm(w) for w in query.split() if len(w) > 2]
        subject = next((w for w in qwords if w not in ADJECTIVES), "")
    else:  # 다른 언어: 영어식 복수형 정리 없이 그대로
        qwords, subject = [w.lower() for w in query.split()], ""
    subs = [_norm(w) if lang == "en" else w.lower() for w in subjects] or ([subject] if subject else [])
    scored = []
    for h in hits:
        v = _best_file(h)
        raw = h.get("tags", "").lower()
        if lang == "en":
            tags = _tag_words(raw)
        else:  # 태그 통째 + 낱말 (일본어·중국어는 띄어쓰기가 없어 글자 포함으로 봄)
            tags = {w for t in raw.split(",") for w in [t.strip(), *t.split()]}
        has = (lambda w: w in raw) if lang in ("ja", "zh") else (lambda w: w in tags)  # noqa: E731
        if not v or _tag_words(raw) & SKIP_TAGS:
            continue
        hit_subject = any(has(w) for w in subs)
        if strict and subs and not hit_subject:
            continue
        # 태그가 검색어와 많이 겹칠수록 관련도 높음 (주인공 단어는 2점)
        match = sum(has(w) for w in qwords) + hit_subject
        scored.append((match, v["height"] > v["width"], h.get("duration", 0) >= min_sec, h, v))
    # 관련도 → 세로 → 충분히 긴 것 순
    scored.sort(key=lambda x: x[:3], reverse=True)
    return scored


def _thumb(h) -> bytes | None:
    urls = ([h["videos"].get(size, {}).get("thumbnail") for size in ("small", "medium", "tiny", "large")]
            if "videos" in h else [h.get("webformatURL"), h.get("previewURL")])
    for url in urls:
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


# ---------- 여러 언어 · 다른 표현 ----------
# 영어 검색어 하나로만 찾으면 인기순 상위 영상이 계속 겹침 → 다른 영어 표현 + 다른 언어 검색어도 섞어 후보를 넓힘.
# (11/20 시험: '강아지 산책'을 영어 1개로 찾으면 100편, 표현·언어 7개를 합치면 서로 다른 영상 324편)
LANGS = {"ko": "한국어", "ja": "일본어", "de": "독일어", "es": "스페인어", "fr": "프랑스어",
         "it": "이탈리아어", "pt": "포르투갈어", "nl": "네덜란드어"}
VARIANT_PICK = 3  # 장면마다 원래 영어 검색어 + 무작위 표현 3개로 검색해 후보를 섞음
_VARIANTS: dict[str, list[tuple[str, str, list[str]]]] = {}

VARIANT_PROMPT = """유튜브 쇼츠 배경 영상을 Pixabay(무료 영상 사이트)에서 찾는 중이야.
{context}
영어 검색어: {query}

같은 장면을 찾을 다른 검색어를 만들어 줘.
- "en": 다른 영어 표현 3개 (같은 뜻의 다른 단어·다른 각도, 예: dog walking park → puppy on leash outdoors, pet stroll sidewalk)
- 그리고 아래 언어마다 그 언어로 자연스러운 검색어 1개씩 (직역보다 그 나라 사람이 실제로 검색할 말): {langs}
- 검색어는 2~3단어, 주인공 명사(강아지 등)를 꼭 넣기
- "subjects": 그 언어로 주인공을 뜻하는 단어 1~3개 — Pixabay 태그에 실제로 붙는 흔한 단어 (예: en ["dog","puppy"], ko ["개","강아지"], ja ["犬","子犬"], de ["hund","welpe"])
JSON으로만 답해: {{"en": [{{"q": "...", "subjects": [...]}}, ...], "ko": {{"q": "...", "subjects": [...]}}, "ja": {{...}}, ...}}"""


def _variants(query: str, context: str) -> list[tuple[str, str, list[str]]]:
    """같은 장면의 다른 검색어들 [(언어, 검색어, 주인공 단어들)]. Gemini 실패하면 빈 목록(영어만으로 진행)."""
    if query in _VARIANTS:
        return _VARIANTS[query]
    out = []
    try:
        from google import genai
        from google.genai import types

        from script_writer import HTTP, _generate

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=HTTP)
        langs = ", ".join(f"{k}({v})" for k, v in LANGS.items())
        resp = _generate(client, JUDGE_MODELS, VARIANT_PROMPT.format(context=context, query=query, langs=langs),
                         types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7))
        data = json.loads(resp.text)
        for lang in ["en", *LANGS]:
            items = data.get(lang) or []
            for it in items if isinstance(items, list) else [items]:
                if not isinstance(it, dict):
                    continue
                q = str(it.get("q") or "").strip()
                subs = [str(w).strip() for w in it.get("subjects") or [] if str(w).strip()]
                if q and q.lower() != query.lower():
                    out.append((lang, q, subs))
    except (Exception, SystemExit) as e:  # 실패해도 영어 검색어만으로 계속
        print(f"    (다른 검색어 만들기 건너뜀: {str(e)[:100]})")
    _VARIANTS[query] = out
    return out


def _mixed_pool(query: str, extra: list, used, min_sec, key) -> list[tuple]:
    """원래 영어 검색어 + 다른 표현·언어 검색어 후보를 번갈아 섞은 목록 (같은 영상은 한 번만)."""
    per = []
    for lang, q, subs in [("en", query, [])] + extra:
        try:
            scored = _pixabay_cands(q, used, min_sec, key, True, lang, subs)
        except requests.RequestException as e:
            print(f"    pixabay 검색 실패({lang} {q}): {e}")
            continue
        top = [x for x in scored if x[0] >= scored[0][0] - 1][:JUDGE_MAX * 2] if scored else []
        random.shuffle(top)
        if top:
            per.append(top)
    pool, seen = [], set()
    for i in range(max(map(len, per), default=0)):
        for top in per:
            if i < len(top) and top[i][3]["id"] not in seen:
                seen.add(top[i][3]["id"])
                pool.append(top[i])
    return pool


def _pixabay_pick(tries: list[tuple[str, bool]], used, min_sec, key, context: str) -> str | None:
    """여러 언어·표현으로 후보를 섞어 먼저 고르고, 안 맞으면 영어 검색어를 차례로 넓혀 가며 다시."""
    judging = bool(context and os.environ.get("GEMINI_API_KEY"))
    queue, tried, first, alts, judged = list(tries), set(), None, 0, 0
    variants = _variants(tries[0][0], context) if judging else []
    if variants:
        extra = random.sample(variants, min(VARIANT_PICK, len(variants)))
        pool = _mixed_pool(tries[0][0], extra, used, min_sec, key)[:JUDGE_MAX]
        if pool:
            idx, alt = _judge(pool, tries[0][0], context)
            judged += 1
            names = ", ".join(f"{lang}:{q}" for lang, q, _ in extra)
            if idx is None:
                return _take(pool[0], used)
            if idx >= 0:
                print(f"    ✔ 여러 검색어({names}) 후보 {len(pool)}개 중 선택 → {pool[idx][3].get('tags', '')[:60]}")
                return _take(pool[idx], used)
            print(f"    ✘ 여러 검색어({names}) 후보 {len(pool)}개 모두 안 맞음 → 영어 검색어로")
            first = pool[0]
            tried.add((tries[0][0], True))  # 원래 검색어는 방금 섞어서 봤음
            if alt:
                queue.insert(0, (alt, True))
                alts += 1
    while queue and judged < JUDGE_ROUNDS:
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
        judged += 1
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


# ---------- 견종 ----------
# 견종 이야기 장면엔 반드시 그 견종이 나와야 함. Pixabay엔 인기 소형견 영상이 거의 없어서(비숑·시츄 0개)
# 영상이 없으면 그 견종 사진(수십~수백 장)에 천천히 확대되는 움직임을 줘서 씀.
# 한국어 이름 → (Pixabay 검색어, 태그에 있어야 할 단어 묶음 — 묶음 하나의 단어가 다 있으면 그 견종)
BREEDS = {
    "골든리트리버": ("golden retriever", [{"golden", "retriever"}]),
    "래브라도리트리버": ("labrador retriever", [{"labrador"}]), "래브라도": ("labrador retriever", [{"labrador"}]),
    "리트리버": ("retriever dog", [{"retriever"}]),
    "토이푸들": ("poodle", [{"poodle"}]), "푸들": ("poodle", [{"poodle"}]),
    "말티즈": ("maltese dog", [{"maltese"}]), "말티푸": ("maltipoo", [{"maltipoo"}]),
    "비숑프리제": ("bichon frise", [{"bichon"}]), "비숑": ("bichon frise", [{"bichon"}]),
    "포메라니안": ("pomeranian", [{"pomeranian"}]), "포메": ("pomeranian", [{"pomeranian"}]),
    "시츄": ("shih tzu", [{"shih", "tzu"}, {"shihtzu"}, {"shih-tzu"}]),
    "치와와": ("chihuahua", [{"chihuahua"}]),
    "웰시코기": ("welsh corgi", [{"corgi"}]), "코기": ("welsh corgi", [{"corgi"}]),
    "시바이누": ("shiba inu", [{"shiba"}]), "시바견": ("shiba inu", [{"shiba"}]), "시바": ("shiba inu", [{"shiba"}]),
    "진돗개": ("jindo dog", [{"jindo"}]),
    "프렌치불독": ("french bulldog", [{"french", "bulldog"}]), "불독": ("bulldog", [{"bulldog"}]),
    "비글": ("beagle", [{"beagle"}]), "닥스훈트": ("dachshund", [{"dachshund"}]),
    "요크셔테리어": ("yorkshire terrier", [{"yorkshire"}, {"yorkie"}]),
    "요크셔": ("yorkshire terrier", [{"yorkshire"}, {"yorkie"}]),
    "시베리안허스키": ("husky dog", [{"husky"}]), "허스키": ("husky dog", [{"husky"}]),
    "보더콜리": ("border collie", [{"border", "collie"}]), "사모예드": ("samoyed", [{"samoyed"}]),
    "슈나우저": ("schnauzer", [{"schnauzer"}]), "퍼그": ("pug dog", [{"pug"}]),
    "저먼셰퍼드": ("german shepherd", [{"shepherd"}]), "셰퍼드": ("german shepherd", [{"shepherd"}]),
    "도베르만": ("doberman", [{"doberman"}]), "코카스파니엘": ("cocker spaniel", [{"cocker"}]),
    "잭러셀테리어": ("jack russell terrier", [{"jack", "russell"}, {"russell"}]),
    "잭러셀": ("jack russell terrier", [{"russell"}]),
    "달마시안": ("dalmatian", [{"dalmatian"}]), "보스턴테리어": ("boston terrier", [{"boston"}]),
    "스피츠": ("spitz dog", [{"spitz"}]), "파피용": ("papillon dog", [{"papillon"}]),
    "베들링턴테리어": ("bedlington terrier", [{"bedlington"}]), "베들링턴": ("bedlington terrier", [{"bedlington"}]),
    "그레이하운드": ("greyhound", [{"greyhound"}]), "말라뮤트": ("alaskan malamute", [{"malamute"}]),
}


def detect_breed(text: str) -> str:
    """글에 나온 견종(한국어 이름) — 긴 이름부터 찾음 ('토이푸들'이 '푸들'보다 먼저). 없으면 ''."""
    flat = (text or "").replace(" ", "")
    return next((k for k in sorted(BREEDS, key=len, reverse=True) if k in flat), "")


BREED_VARIANT_PROMPT = """유튜브 쇼츠 배경으로 {breed}({q}) 영상·사진을 Pixabay(무료 영상 사이트)에서 찾는 중이야.
{context}

이 견종이 꼭 나오는 다른 검색어를 만들어 줘.
- "en": 다른 영어 표현 3개 (예: chihuahua puppy, chihuahua dog walking — 장면 내용에 맞는 행동·장소를 붙여도 좋음)
- 그리고 아래 언어마다 그 언어로 자연스러운 검색어 1개씩: {langs}
- 검색어는 1~3단어, 견종 이름을 꼭 넣기
- "names": 그 언어로 이 견종 이름만 1~3개 (개·강아지·dog·perro 같은 일반 단어 금지) — Pixabay 태그에 실제로 붙는 표기 (예: ko ["치와와"], ja ["チワワ"], de ["chihuahua"], 골든리트리버 ko ["골든 리트리버","골든리트리버"])
JSON으로만 답해: {{"en": [{{"q": "...", "names": [...]}}, ...], "ko": {{"q": "...", "names": [...]}}, "ja": {{...}}, ...}}"""


# Gemini가 견종 이름에 '강아지 · dog · perro' 같은 일반 단어를 섞으면 다른 견종이 통과함 → 견종 이름만 남김
GENERIC_KO_JA = {"개", "강아지", "반려견", "애완견", "소형견", "중형견", "대형견", "강아지들",
                 "犬", "いぬ", "イヌ", "子犬", "小型犬", "中型犬", "大型犬", "ペット", "わんちゃん"}


def _breed_name_ok(name: str, breed: str) -> bool:
    """영어·유럽 언어 표기는 영어 견종 단어(chihuahua · bichon …)가 들어 있어야, 한국어·일본어 표기는 일반 단어가 아니어야 통과."""
    if len(name) < 2:
        return False
    if name in GENERIC_KO_JA:
        return False
    if any("가" <= c <= "힣" for c in name):  # 한국어: 견종 이름 앞 두 글자가 들어 있어야 (치와와 · 골든 리트리버)
        return breed[:2] in name.replace(" ", "")
    if any("぀" <= c <= "鿿" for c in name):  # 일본어: 일반 단어만 아니면 (チワワ · ビション)
        return True
    # 영어·유럽 언어: 영어 견종 단어 묶음이 다 들어 있어야 (golden retriever O, retriever 만 X)
    return any(all(w in name for w in g) for g in BREEDS[breed][1])


def _breed_variants(breed: str, context: str) -> list[tuple[str, str, list[str]]]:
    """견종이 들어간 다른 표현·다른 언어 검색어 [(언어, 검색어, 그 언어의 견종 이름들)]. 실패하면 빈 목록."""
    q = BREEDS[breed][0]
    key = f"breed:{breed}"
    if key in _VARIANTS:
        return _VARIANTS[key]
    out = []
    try:
        from google import genai
        from google.genai import types

        from script_writer import HTTP, _generate

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=HTTP)
        langs = ", ".join(f"{k}({v})" for k, v in LANGS.items())
        resp = _generate(client, JUDGE_MODELS,
                         BREED_VARIANT_PROMPT.format(breed=breed, q=q, context=context, langs=langs),
                         types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7))
        data = json.loads(resp.text)
        for lang in ["en", *LANGS]:
            items = data.get(lang) or []
            for it in items if isinstance(items, list) else [items]:
                if not isinstance(it, dict):
                    continue
                vq = str(it.get("q") or "").strip()
                names = [n for n in (str(w).strip().lower() for w in it.get("names") or []) if _breed_name_ok(n, breed)]
                if vq and names and vq.lower() != q.lower():
                    out.append((lang, vq, names))
    except (Exception, SystemExit) as e:  # 실패해도 영어 견종 이름만으로 계속
        print(f"    (견종 다른 검색어 만들기 건너뜀: {str(e)[:100]})")
    _VARIANTS[key] = out
    return out


def _breed_hits(kind: str, breed: str, used, key, allow_recent: bool, lang="en", q="", names=()) -> list[dict]:
    """그 견종 태그가 붙은 영상/사진. lang·q·names 를 주면 그 언어 검색어로 찾고 그 언어 견종 이름으로 확인."""
    base_q, groups = BREEDS[breed]
    url = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    params = {"key": key, "q": (q or base_q)[:100], "lang": lang, "per_page": 100, "safesearch": "true",
              "order": "popular"}
    params.update({"video_type": "film"} if kind == "video" else {"image_type": "photo"})
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    tag = "pixabay" if kind == "video" else "pixabay-img"
    recent = set() if allow_recent else _recent_ids()
    out = []
    for h in r.json().get("hits", []):
        raw = h.get("tags", "").lower()
        tags = _tag_words(raw)
        if (tag, h["id"]) in used or f"{tag}:{h['id']}" in recent or tags & SKIP_TAGS:
            continue
        if lang == "en" and not names:
            ok = any(g <= tags for g in groups)
        else:  # 다른 언어는 태그가 그 언어로 옴 → 견종 이름이 태그 글자에 들어 있는지
            ok = any(n in raw for n in names) or any(g <= tags for g in groups)
        if ok:
            out.append(h)
    return out


def _breed_mixed(kind: str, breed: str, used, key, allow_recent: bool, extra: list) -> list[dict]:
    """영어 견종 이름 + 다른 표현·언어 검색 결과를 번갈아 섞음 (같은 영상·사진은 한 번만)."""
    per = []
    for lang, q, names in [("en", "", [])] + extra:
        try:
            hits = _breed_hits(kind, breed, used, key, allow_recent, lang, q, names)
        except requests.RequestException as e:
            print(f"    pixabay 견종 검색 실패({lang} {q}): {e}")
            continue
        random.shuffle(hits)
        if hits:
            per.append(hits)
    out, seen = [], set()
    for i in range(max(map(len, per), default=0)):
        for hits in per:
            if i < len(hits) and hits[i]["id"] not in seen:
                seen.add(hits[i]["id"])
                out.append(hits[i])
    return out


def _photo_clip(img: Path, sec: float) -> str:
    """사진 → 1080x1920 영상 (흐린 배경 위에 사진 전체, 천천히 확대)."""
    import subprocess

    from shorts_maker import FFMPEG
    n = max(int((sec + 1) * 30), 60)
    out = img.with_name(f"{img.stem}_{n}.mp4")
    if not out.exists():
        vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "boxblur=24:2,eq=brightness=-0.08[bg];[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2,scale=2160:3840,"
              f"zoompan=z='1+0.08*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30,"
              "format=yuv420p")
        subprocess.run([FFMPEG, "-y", "-loop", "1", "-framerate", "30", "-i", str(img), "-frames:v", str(n),
                        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)],
                       check=True, capture_output=True)
    return str(out)


def _breed_clip(breed: str, used, min_sec, key, context: str) -> str | None:
    """그 견종 영상 → 없으면 그 견종 사진. 최근 영상에 쓴 것밖에 없으면 다시 씀 (다른 견종보다는 나음)."""
    q = BREEDS[breed][0]
    ctx = f"{context}\n⚠️ 이 장면엔 반드시 {breed}({q})가 보여야 함. 다른 견종이면 0."
    judging = bool(context and os.environ.get("GEMINI_API_KEY"))
    # 영어 견종 이름 + 다른 표현·언어(예: 치와와 · チワワ · perro chihuahua) 무작위 3개를 섞어 후보를 넓힘
    variants = _breed_variants(breed, context) if judging else []
    extra = random.sample(variants, min(VARIANT_PICK, len(variants))) if variants else []
    if extra:
        print(f"    🐶 {breed} 검색어: {q}, " + ", ".join(f"{lang}:{vq}" for lang, vq, _ in extra))
    rejected = set()  # 이미 안 맞는다고 본 후보는 다시 판단 안 함
    for kind in ("video", "photo"):
        for allow_recent in (False, True):
            hits = [h for h in _breed_mixed(kind, breed, used, key, allow_recent, extra)
                    if (kind, h["id"]) not in rejected]
            if kind == "video":
                hits = [h for h in hits if _best_file(h)]
            if not hits:
                continue
            pool = [(0, 0, 0, h, _best_file(h) if kind == "video" else None) for h in hits[:JUDGE_MAX]]
            idx, _ = _judge(pool, q, ctx) if judging else (None, "")
            if idx == -1:
                rejected.update((kind, c[3]["id"]) for c in pool)
                print(f"    ✘ {breed} {'영상' if kind == 'video' else '사진'} {len(pool)}개 모두 장면과 안 맞음")
                continue
            pick = pool[idx if idx is not None else 0]
            if kind == "video":
                print(f"    🐶 {breed} 영상 사용")
                return _take(pick, used)
            h = pick[3]
            used.add(("pixabay-img", h["id"]))
            PICKED.append(f"pixabay-img:{h['id']}")
            img = Path(_download(h.get("largeImageURL") or h["webformatURL"], CACHE / "pixabay_img" / f"{h['id']}.jpg"))
            print(f"    🐶 {breed} 영상이 없어 {breed} 사진으로")
            return _photo_clip(img, min_sec)
    return None


def _any_dog_clip(used, min_sec, key) -> str | None:
    """마지막 안전망 — 장면에 맞는 영상이 하나도 없어도 빈 화면 대신 강아지가 나오는 아무 영상 · 사진(일러스트 포함).
    판단 없이, 최근에 쓴 것도 허용. (11/23 사장님: 닥스훈트 영상이 빈 화면으로 나옴 → 무조건 강아지 화면)"""
    dog = {"dog", "dogs", "puppy", "puppies", "pet", "canine", "hound", "terrier"}
    for q in ("dog", "puppy", "cute dog", "dog walking"):
        for vtype in ("film", "all"):
            r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                             params={"key": key, "q": q, "per_page": 100, "safesearch": "true",
                                     "order": "popular", "video_type": vtype})
            r.raise_for_status()
            hits = [h for h in r.json().get("hits", [])
                    if ("pixabay", h["id"]) not in used and _best_file(h)
                    and _tag_words(h.get("tags", "").lower()) & dog
                    and not _tag_words(h.get("tags", "").lower()) & SKIP_TAGS]
            if hits:
                hits.sort(key=lambda h: h.get("duration", 0) >= min_sec, reverse=True)
                h = random.choice(hits[:15])
                print(f"    🐕 맞는 영상이 없어 일반 강아지 영상으로 ({q})")
                return _take((0, 0, 0, h, _best_file(h)), used)
    for q in ("dog", "puppy", "dog illustration"):
        r = requests.get("https://pixabay.com/api/", timeout=20,
                         params={"key": key, "q": q, "per_page": 100, "safesearch": "true",
                                 "order": "popular", "image_type": "all", "orientation": "vertical"})
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", [])
                if ("pixabay-img", h["id"]) not in used
                and _tag_words(h.get("tags", "").lower()) & dog]
        if hits:
            h = random.choice(hits[:20])
            used.add(("pixabay-img", h["id"]))
            img = Path(_download(h.get("largeImageURL") or h["webformatURL"], CACHE / "pixabay_img" / f"{h['id']}.jpg"))
            print(f"    🐕 맞는 영상이 없어 일반 강아지 사진·일러스트로 ({q})")
            return _photo_clip(img, min_sec)
    return None


def _fetch_clip_main(query: str, used: set, min_sec: float = 0, context: str = "", breed: str = "") -> str | None:
    """검색어에 맞는 영상 1개를 받아 로컬 경로 반환 (없으면 None → 그라데이션 배경).
    context = 영상 제목 + 이 장면의 문장 — 있으면 Gemini가 내용에 맞는 영상을 고름.
    breed = 이 장면의 견종(한국어, detect_breed) — 있으면 그 견종 영상·사진만."""
    load_env()
    if breed in BREEDS and os.environ.get("PIXABAY_API_KEY"):
        try:
            path = _breed_clip(breed, used, min_sec, os.environ["PIXABAY_API_KEY"], context)
            if path:
                return path
        except Exception as e:  # 견종 찾기가 실패해도 영상 제작은 계속 (일반 검색으로)
            print(f"    ({breed} 찾기 실패, 일반 검색으로: {str(e)[:100]})")
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


def fetch_clip(query: str, used: set, min_sec: float = 0, context: str = "", breed: str = "") -> str | None:
    """장면에 맞는 영상 → 없으면 일반 강아지 영상 · 사진 (빈 화면은 마지막 수단)."""
    path = _fetch_clip_main(query, used, min_sec, context, breed)
    if path:
        return path
    load_env()
    key = os.environ.get("PIXABAY_API_KEY")
    if key:
        try:
            return _any_dog_clip(used, min_sec, key)
        except Exception as e:
            print(f"    (일반 강아지 찾기 실패: {str(e)[:100]})")
    return None
