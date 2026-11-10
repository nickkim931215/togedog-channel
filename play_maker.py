"""투게독 플레이 쇼츠: 게임 촬영 모드(togedog.app/?record)를 로봇이 조작해 찍고 → 장면 이어 붙이기 · 자막 · 게임 음악.

영상마다 느낌이 다르게 (씨앗 하나로 전부 정함 — 같은 씨앗이면 같은 영상):
  연출 캐릭터 수(한산 ~ 북적) · 낮/저녁 · 장면 3~4개 고르기와 순서 · 확대/축소 · 내 캐릭터(🎲 랜덤) · 자막 문구 · 음악 시작 위치
가상 시계(vclock.js): 느린 컴퓨터(클라우드는 그래픽 카드 없음)라도 한 장 = 1/30초로 찍어 영상이 매끄럽다.

  python play_maker.py [--seed 42] [--out output/play.mp4] [--base http://localhost:3123]
"""
import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from settings import BASE
from shorts_maker import FFMPEG

APP = os.environ.get("TOGEDOG_BASE", "https://togedog.app")
VCLOCK = (BASE / "vclock.js").read_text(encoding="utf-8")
BGM = BASE / "bgm" / "game.mp3"
FONT = BASE / "fonts" / "Pretendard-ExtraBold.otf"
FPS = 30
W, H = 540, 960  # × 2 = 1080x1920
DPR = float(os.environ.get("PLAY_DPR", "2"))  # 시험할 땐 1 (4배 빠름)

# 내 캐릭터 이름 (🎲 랜덤 이름 대신 — 영상에 어울리는 것만)
OWNERS = ["몽이맘", "콩이아빠", "보리집사", "초코언니", "두부형", "하루맘", "산책러", "댕댕집사", "뭉치누나", "까미아빠"]
PETS = ["몽이", "콩이", "보리", "초코", "두부", "하루", "뭉치", "까미", "토리", "구름"]


class Cam:
    """가상 시계로 한 장씩 찍기."""

    def __init__(self, page, folder: Path):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        self.page, self.dir, self.n = page, folder, 0
        self.cdp = page.context.new_cdp_session(page)
        page.evaluate("window.__vt.freeze()")

    def step(self):
        self.page.evaluate(f"window.__vt.step({1000 / FPS})")

    def frames(self, secs: float):
        for _ in range(round(secs * FPS)):
            self.step()
            data = self.cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 88, "optimizeForSpeed": True})["data"]
            (self.dir / f"f{self.n:06d}.jpg").write_bytes(base64.b64decode(data))
            self.n += 1

    def skip(self, secs: float):
        for _ in range(round(secs * FPS)):
            self.step()

    def save(self, out: Path) -> float:
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", "f%06d.jpg",
                        "-vf", "scale=1080:1920", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out.resolve())],
                       cwd=self.dir, check=True)
        return self.n / FPS


def click(page, loc):
    loc.first.wait_for(timeout=15000)
    loc.first.click(force=True)  # 가상 시계라 반짝이는 버튼이 '멈출' 때까지 기다리지 않게


def hold(page, cam, key: str, secs: float, shoot=True):
    page.keyboard.down(key)
    (cam.frames if shoot else cam.skip)(secs)
    page.keyboard.up(key)


# ---------- 장면: (출발 자리 후보, 자막 후보, 찍기) — 길이 5~7초 ----------

def s_walk(page, cam, r):
    cam.skip(1)
    hold(page, cam, "ArrowUp", r.uniform(2.5, 3.5))
    hold(page, cam, r.choice(["ArrowLeft", "ArrowRight"]), r.uniform(1, 1.5))
    cam.frames(1.5)


def s_talk(page, cam, r):
    cam.skip(1)
    cam.frames(0.8)
    click(page, page.get_by_role("button", name="말하기", exact=True))
    cam.frames(0.6)
    click(page, page.get_by_role("button", name="안녕하세요 👋"))
    page.keyboard.press("Escape")
    cam.frames(4)


def s_photo(page, cam, r):
    hold(page, cam, "ArrowDown", 0.25, shoot=False)  # 강을 등지게 → 강 · 다리가 배경
    cam.skip(0.8)
    click(page, page.get_by_role("button", name="사진", exact=True))
    cam.skip(1.5)
    cam.frames(2.2)
    click(page, page.get_by_role("button", name="사진 찍기"))
    cam.frames(1.8)


def s_futsal(page, cam, r):
    click(page, page.get_by_role("button", name="풋살 하기"))
    cam.skip(1.2)
    cam.frames(6.5)


def s_dogpark(page, cam, r):
    click(page, page.get_by_role("button", name="가까이", exact=True))
    click(page, page.get_by_role("button", name="강아지 놀게 하기"))
    cam.skip(1)
    cam.frames(5.5)


def s_river(page, cam, r):
    cam.skip(1)
    hold(page, cam, r.choice(["ArrowLeft", "ArrowRight"]), r.uniform(5, 6.5))


def s_auto(page, cam, r):
    click(page, page.get_by_role("button", name="자동 산책", exact=True))
    cam.skip(1.5)
    cam.frames(r.uniform(5, 6.5))


def s_ending(page, cam, r):
    click(page, page.get_by_role("button", name="멀리", exact=True))
    cam.skip(1.2)
    cam.frames(4.5)


SCENES = {
    "walk": (["0,16", "0,12", "-2,6"], ["우리 아이와 동네 산책 🐾", "오늘도 산책 나왔어요", "강아지랑 3D 산책"], s_walk),
    "talk": (["-2,6", "2,4"], ["지나가는 이웃에게 인사!", "동네 친구들과 인사해요", "안녕하세요~ 👋"], s_talk),
    "photo": (["-92,-35"], ["우리 아이 사진도 찰칵 📸", "한강 배경으로 인생샷"], s_photo),
    "futsal": (["futsal"], ["풋살장에서 한 판 ⚽", "강아지들은 펜스 밖에서 응원 중"], s_futsal),
    "dogpark": (["-17,51.6"], ["벤치에 앉으면 강아지들끼리 놀아요", "반려견 놀이터 🐶"], s_dogpark),
    "river": (["-40,-46", "-60,-40"], ["한강 따라 걷기", "강바람 맞으며 산책"], s_river),
    "auto": (["0,16", "-40,-46"], ["손 놓고 자동 산책도 돼요", "자동 산책 모드"], s_auto),
}
ENDING_AT = ["0,12", "0,16"]


def open_game(browser, at: str, cfg: dict, r: random.Random):
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DPR, is_mobile=True, has_touch=True,
                              locale="ko-KR")
    ctx.add_init_script("""localStorage.setItem("togedog-install-later", String(Date.now()));
localStorage.setItem("togedog-lang", "ko"); localStorage.setItem("togedog-lite", "0");""")
    ctx.add_init_script(VCLOCK)
    page = ctx.new_page()
    q = f"record&seed={cfg['seed']}&n={cfg['n']}&talk={cfg['talk']}&at={at}&time={cfg['time']}"
    page.goto(f"{cfg['base']}/?{q}", wait_until="domcontentloaded", timeout=120000)
    go = page.get_by_role("button", name="투게독 3D 프로필 생성하기")
    go.wait_for(timeout=180000)
    # 내 캐릭터: 🎲 랜덤 (견종 · 옷) + 이름
    page.evaluate(f"Math.random = (() => {{ let s = {cfg['seed']} >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }})()")
    page.get_by_role("button", name="🎲 랜덤").first.click()
    for label, name in (("투게독시민", cfg["owner"]), ("댕댕이", cfg["pet"])):  # 이름 칸 (자리 글씨 = 기본 이름)
        box = page.get_by_placeholder(label).first
        if box.count() and box.is_visible():
            box.fill(name)
    go.click()
    page.get_by_role("button", name="설정", exact=True).wait_for(timeout=240000)
    page.wait_for_timeout(9000)  # 지도 · 풀 · 연출 캐릭터 자리 잡기
    for _ in range(3):  # 알림 카드 닫기
        b = page.get_by_role("button", name="닫기")
        if b.count() and b.first.is_visible():
            b.first.click(force=True)
            page.wait_for_timeout(400)
    zoom = r.choice([None, None, "가까이", "멀리"])  # 확대 / 축소 섞기 (버튼 이름 = 가까이 · 멀리, 3단계 중 가운데에서 시작)
    if zoom:
        page.get_by_role("button", name=zoom, exact=True).first.click(force=True)
        page.wait_for_timeout(300)
    return ctx, page


def plan(seed: int, base: str = APP) -> dict:
    r = random.Random(seed)
    n = r.choice([r.randint(6, 14), r.randint(18, 30), r.randint(32, 50)])  # 한산 · 보통 · 북적
    picks = r.sample(list(SCENES), r.choice([3, 4]))
    return {
        "seed": seed, "base": base, "n": n, "talk": 8,
        "time": r.choices(["day", "night"], weights=[7, 3])[0],
        "owner": r.choice(OWNERS), "pet": r.choice(PETS),
        "scenes": [(s, r.choice(SCENES[s][0]), r.choice(SCENES[s][1])) for s in picks],
        "ending_at": r.choice(ENDING_AT),
        "bgm_from": r.randint(0, 600),
    }


def headline(cfg: dict) -> str:
    n = cfg["n"]
    return "오늘은 한산한 동네 🌿" if n < 15 else "오늘 동네 북적북적! 🐶🐶" if n >= 32 else "오늘도 이웃들과 함께"


def shoot(cfg: dict, work: Path, headed=False) -> list[tuple[Path, str | None]]:
    from playwright.sync_api import sync_playwright
    r = random.Random(cfg["seed"] ^ 0x5bd1)
    clips = []
    with sync_playwright() as p:
        exe = os.environ.get("CHROME_PATH") or (r"C:\Program Files\Google\Chrome\Application\chrome.exe" if os.name == "nt" else None)
        browser = p.chromium.launch(executable_path=exe, headless=not headed,
                                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        jobs = [(s, at, cap, SCENES[s][2]) for s, at, cap in cfg["scenes"]] + [("ending", cfg["ending_at"], None, s_ending)]
        for i, (name, at, cap, fn) in enumerate(jobs):
            t0 = time.time()
            ctx, page = open_game(browser, at, cfg, r)
            try:
                cam = Cam(page, work / name)
                fn(page, cam, r)
                out = work / f"{i:02d}_{name}.mp4"
                secs = cam.save(out)
                clips.append((out, cap))
                print(f"  🎬 {name}: {secs:.1f}초 ({time.time() - t0:.0f}초 걸림)")
            except Exception as e:  # 한 장면이 안 되면 빼고 계속
                print(f"  ⚠ {name} 실패: {str(e).splitlines()[0]}")
                page.screenshot(path=str(work / f"{name}-error.png"))
            ctx.close()
        browser.close()
    if len(clips) < 3 or clips[-1][1] is not None:
        raise RuntimeError(f"촬영된 장면이 부족해요 ({len(clips)}개)")
    return clips


def _dur(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True, errors="ignore").stderr
    h, m, s = out.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def edit(clips: list[tuple[Path, str | None]], cfg: dict, out: Path):
    """자르기 · 장면 사이 부드럽게(xfade) · 자막 · 마무리 화면 · 게임 음악."""
    fade = 0.4
    font = FONT.name  # 필터에 C: 가 들어가면 깨져서 글꼴 폴더에서 실행
    # 자막 글꼴(Pretendard)엔 이모지가 없어 네모로 나옴 → 자막에선 이모지 빼기 (제목 · 설명엔 그대로)
    plain = lambda s: re.sub(r"[\U0001F000-\U0001FFFF☀-➿️‍]", "", s).strip()
    esc = lambda s: plain(s).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def text(t, size, color, border, y, extra=""):
        return (f"drawtext=fontfile='{font}':text='{esc(t)}':fontsize={size}:fontcolor={color}:borderw={max(3, size // 11)}"
                f":bordercolor={border}:x=(w-text_w)/2:y={y}{extra}")

    args, f, lens = [FFMPEG, "-y", "-loglevel", "error"], [], []
    for path, _ in clips:
        args += ["-i", str(path.resolve())]
        lens.append(_dur(path))
    args += ["-ss", str(cfg["bgm_from"]), "-i", str(BGM.resolve())]
    for i, (_, cap) in enumerate(clips):
        chain = ["setpts=PTS-STARTPTS", "scale=1080:1920", f"fps={FPS}", "format=yuv420p"]
        if i == 0:
            chain.append(text(headline(cfg), 70, "white", "0xf97316", "h*0.16", ":enable='lt(t,2.6)'"))
        if cap:
            chain.append(text(cap, 72, "white", "0x3b2a1a", "h*0.60", ":alpha='if(lt(t,0.25),t/0.25,1)'"))
        if i == len(clips) - 1:
            chain += ["drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill:enable='gte(t,0.6)'",
                      text("투게독", 170, "white", "0xf97316", "h*0.36", ":enable='gte(t,0.6)'"),
                      text("우리 아이와 함께 걷는 동네", 64, "white", "0x3b2a1a", "h*0.36+220", ":enable='gte(t,0.9)'"),
                      text("togedog.app", 84, "0xfde68a", "0x3b2a1a", "h*0.36+330", ":enable='gte(t,1.2)'")]
        f.append(f"[{i}:v]{','.join(chain)}[v{i}]")
    prev, total = "v0", lens[0]
    for i in range(1, len(clips)):
        f.append(f"[{prev}][v{i}]xfade=transition=fade:duration={fade}:offset={total - fade:.3f}[x{i}]")
        prev, total = f"x{i}", total + lens[i] - fade
    f.append(f"[{len(clips)}:a]atrim=duration={total:.3f},afade=t=in:d=0.5,afade=t=out:st={total - 1.5:.3f}:d=1.5,volume=0.8[a]")
    args += ["-filter_complex", ";".join(f), "-map", f"[{prev}]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
             "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out.resolve())]
    subprocess.run(args, cwd=FONT.parent, check=True)
    return total


TITLES = ["강아지랑 한강 산책하는 게임 🐶", "우리 강아지랑 동네 한 바퀴 🐾", "강아지 키우면 꼭 해봐야 할 게임",
          "이웃 댕댕이들과 산책하는 3D 게임", "오늘의 투게독 산책 로그 🐕", "강아지랑 같이 걷는 동네가 있다?"]


def make_play(out: Path, seed: int | None = None, base: str = APP, headed=False, scenes: list[str] | None = None) -> dict:
    seed = seed if seed is not None else int(time.time()) % 1_000_000
    cfg = plan(seed, base)
    if scenes:  # 시험: 장면 직접 고르기
        r = random.Random(seed)
        cfg["scenes"] = [(s, r.choice(SCENES[s][0]), r.choice(SCENES[s][1])) for s in scenes]
    print(f"▶ 플레이 영상 씨앗 {seed}: 사람 {cfg['n']}명 · {cfg['time']} · 장면 {[s for s, _, _ in cfg['scenes']]}")
    work = BASE / "cache" / f"play_{seed}"
    clips = shoot(cfg, work, headed)
    secs = edit(clips, cfg, out)
    print(f"  ✅ {out.name} ({secs:.1f}초)")
    caps = [c for _, c in clips if c]
    r = random.Random(seed)
    return {
        "title": r.choice(TITLES),
        "sentences": [headline(cfg)] + caps,
        "description": "내 강아지와 함께 걷는 3D 산책 게임, 투게독! 이웃 강아지들을 만나고, 인사하고, 사진도 찍어요. "
                       "설치 없이 폰 브라우저에서 바로 할 수 있어요.",
        "tags": ["강아지게임", "산책", "반려견", "한강", "모바일게임"],
        "seed": seed, "cast": cfg["n"], "time": cfg["time"], "scenes": [s for s, _, _ in cfg["scenes"]],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--out", default=str(BASE / "output" / "play_test.mp4"))
    ap.add_argument("--base", default=APP)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--scenes", help="쉼표로 (시험용)")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(exist_ok=True)
    print(json.dumps(make_play(Path(a.out), a.seed, a.base, a.headed, a.scenes.split(",") if a.scenes else None), ensure_ascii=False, indent=1))
