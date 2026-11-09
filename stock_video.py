"""무료 스톡 영상 검색/다운로드 (Pixabay / Pexels, 로컬 캐시).

.env에 PIXABAY_API_KEY 또는 PEXELS_API_KEY 중 있는 것을 사용 (둘 다 있으면 Pexels 먼저).
"""
import os
import random

import requests

from settings import BASE, load_env

CACHE = BASE / "cache"


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
    videos = [v for v in r.json().get("videos", []) if ("pexels", v["id"]) not in used]
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
        return _download(f["link"], CACHE / "pexels" / f"{v['id']}_{f['height']}.mp4")
    return None


# ---------- Pixabay ----------
# 검색어 첫 단어 = 장면의 주인공. 태그에 이게 없으면 버림 (예: "cute puppy tilting head" → 다람쥐 'cute, head' 배제)
SYNONYMS = {"puppy": "dog", "puppies": "dog", "doggy": "dog", "kitten": "cat", "kitty": "cat", "kittens": "cat"}
SKIP_TAGS = {"cartoon", "animation", "illustration", "render", "3d"}
ADJECTIVES = {"cute", "happy", "little", "small", "big", "beautiful", "young", "old", "calm", "cozy", "bright", "golden"}


def _norm(w: str) -> str:
    w = w.lower().strip()
    w = SYNONYMS.get(w, w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _tag_words(tags: str) -> set[str]:
    return {_norm(w) for t in tags.split(",") for w in t.split()}


def _pixabay(query, used, min_sec, key, strict=True):
    r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                     params={"key": key, "q": query[:100], "per_page": 30, "safesearch": "true", "order": "popular",
                             "video_type": "film"})  # 실사만 (일러스트/애니메이션 제외)
    r.raise_for_status()
    hits = [h for h in r.json().get("hits", []) if ("pixabay", h["id"]) not in used]

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
    # 관련도 → 세로 → 충분히 긴 것 순, 상위 몇 개 중 랜덤
    scored.sort(key=lambda x: x[:3], reverse=True)
    top = [x for x in scored if x[0] == scored[0][0]][:4] if scored else []
    random.shuffle(top)
    for *_, h, v in top:
        used.add(("pixabay", h["id"]))
        return _download(v["url"], CACHE / "pixabay" / f"{h['id']}_{v['width']}x{v['height']}.mp4")
    return None


def fetch_clip(query: str, used: set, min_sec: float = 0) -> str | None:
    """검색어에 맞는 영상 1개를 받아 로컬 경로 반환 (없으면 None → 그라데이션 배경)."""
    load_env()
    providers = []
    if os.environ.get("PEXELS_API_KEY"):
        providers.append((_pexels, os.environ["PEXELS_API_KEY"]))
    if os.environ.get("PIXABAY_API_KEY"):
        providers.append((_pixabay, os.environ["PIXABAY_API_KEY"]))
    words = query.split()
    for fn, key in providers:
        # 주인공 단어가 태그에 있는 영상만 → 검색어 줄여서(주인공+1단어, 주인공만) → 그래도 없으면 제한 없이
        words = [w for w in words if w.lower() not in ADJECTIVES] or words
        tries = [(q, True) for q in dict.fromkeys([query, " ".join(words[:2]), " ".join(words[:1])])] + [(query, False)]
        for q, strict in tries:
            try:
                path = fn(q, used, min_sec, key, strict) if fn is _pixabay else fn(q, used, min_sec, key)
            except requests.RequestException as e:
                print(f"    {fn.__name__[1:]} 검색 실패({q}): {e}")
                break
            if path:
                return path
    return None
