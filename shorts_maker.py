"""대본 → 9:16 세로 쇼츠 MP4 생성.

배경: 문장별 Pexels 스톡 영상(없으면 움직이는 그라데이션) + 느린 패닝 + 크로스페이드
음성: Gemini TTS 사람 목소리(4종 랜덤) + Whisper 단어 타이밍, 실패 시 edge-tts
자막: ASS(libass) 단어 단위 하이라이트 + 팝 애니메이션, 상단 제목
음악: bgm/ 폴더의 음원 중 하나를 낮은 볼륨으로 믹스 (있을 때만)
"""
import asyncio
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts

from office import report
from settings import BASE
from stock_video import fetch_clip

W, H = 1080, 1920
FPS = 30


def _ffmpeg() -> str:
    """Windows: imageio-ffmpeg 내장본 / Linux(클라우드): 시스템 ffmpeg (libass 포함)."""
    if os.environ.get("FFMPEG_BIN"):
        return os.environ["FFMPEG_BIN"]
    if os.name != "nt" and shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = _ffmpeg()
FONT_FILE = BASE / "fonts" / "Pretendard-ExtraBold.otf"  # 프로젝트 내장 한글 폰트 (OFL, 어느 OS든 동일)
FONT_NAME = "Pretendard ExtraBold"  # 기본 패밀리명(nameID 1) — Linux fontconfig는 "Pretendard"로 못 찾아 □로 깨짐
DEFAULT_VOICE = "ko-KR-SunHiNeural"  # 남성: ko-KR-InJoonNeural
PAUSE_SEC = 0.35   # 문장 사이 여백
XFADE = 0.6        # 장면 전환 길이
BGM_DIR = BASE / "bgm"
BGM_VOLUME = 0.12
COUNTDOWN = 3      # 퀴즈 문제 뒤 무음 카운트다운(초)
GOLD = "&H55D7FF&"
FALLBACK_SCENES = ["dog running park", "dog walking leash street", "dog sleeping sofa", "dog playing ball grass",
                   "dog tilting head indoor", "dog river walk sunset", "dog puppy playing", "dog happy beach"]
GRADIENTS = [("0x0f1a2e", "0x3a2a4d"), ("0x10241f", "0x2d3f2a"), ("0x1c1427", "0x452a3a"), ("0x0d1b2a", "0x1b3a4b")]


# ---------- 유틸 ----------
def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg 실패:\n{r.stderr[-1500:]}")


def media_duration(path) -> float:
    r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True, errors="ignore")
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# ---------- 음성 ----------
async def _tts(text: str, voice: str, out: Path) -> list[tuple[float, float, str]]:
    words = []
    comm = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    with open(out, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
    return words


def attach_punctuation(sentence: str, words):
    """WordBoundary는 문장부호를 빼고 주므로 원문에서 단어 뒤 부호를 다시 붙임."""
    out, pos = [], 0
    for start, dur, w in words:
        i = sentence.find(w, pos)
        if i < 0:
            out.append((start, dur, w))
            continue
        j = i + len(w)
        while j < len(sentence) and not sentence[j].isspace() and not sentence[j].isalnum():
            j += 1
        out.append((start, dur, sentence[i:j]))
        pos = j
    return out


# ---------- 자막(ASS) ----------
def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def chunk_words(words, max_chars=11, max_words=3):
    """쇼츠식으로 2~3단어씩 끊어서 보여줌. 쉼표/마침표에서도 끊음."""
    chunks, cur = [], []
    for w in words:
        if cur and (sum(len(x[2]) for x in cur) + len(w[2]) > max_chars or len(cur) >= max_words):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[,.!?…]$", w[2]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def quiz_events(quiz: list[dict], timeline) -> tuple[list[str], set, dict]:
    """퀴즈 연출: 문제 카드(Q번호+문제, 풀이 끝까지 유지) → 3·2·1 카운트다운 + 줄어드는 막대 → 금색 정답 팝업.
    반환 (ASS 줄들, 자막 숨길 문장, 자막 위치를 내릴 문장 {idx: y})."""
    lines, hide, low = [], set(), {}
    for q in quiz:
        at = q["at"]
        card0, card1 = timeline[at][1], timeline[at + 3][1]
        fad = "\\fad(200,200)"
        lines.append(f"Dialogue: 2,{ass_time(card0)},{ass_time(card1)},QNum,,0,0,0,,{{\\an8\\pos(540,570){fad}}}Q{q['n']}")
        lines.append(f"Dialogue: 2,{ass_time(card0)},{ass_time(card1)},Card,,0,0,0,,"
                     f"{{\\an8\\pos(540,645){fad}\\fscx92\\fscy92\\t(0,200,\\fscx100\\fscy100)}}{ass_escape(q['q'])}")
        c0 = q["count_at"]
        bar = "m 0 0 l 600 0 600 12 0 12{\\p0}"
        lines.append(f"Dialogue: 1,{ass_time(c0)},{ass_time(c0 + COUNTDOWN)},Shape,,0,0,0,,"
                     f"{{\\an7\\pos(240,1290)\\1c&HFFFFFF&\\1a&HB0&\\p1}}{bar}")
        lines.append(f"Dialogue: 2,{ass_time(c0)},{ass_time(c0 + COUNTDOWN)},Shape,,0,0,0,,"
                     f"{{\\an7\\pos(240,1290)\\1c{GOLD}\\t(0,{COUNTDOWN * 1000},\\fscx0)\\p1}}{bar}")
        for k in range(COUNTDOWN):
            lines.append(f"Dialogue: 3,{ass_time(c0 + k)},{ass_time(c0 + k + 0.95)},Count,,0,0,0,,"
                         f"{{\\an5\\pos(540,1060)\\fscx150\\fscy150\\alpha&H80&"
                         f"\\t(0,220,\\fscx100\\fscy100\\alpha&H00&)\\fad(0,150)}}{COUNTDOWN - k}")
        r0 = q["reveal_at"]
        lines.append(f"Dialogue: 3,{ass_time(r0)},{ass_time(card1)},QNum,,0,0,0,,{{\\an5\\pos(540,890)\\fad(150,200)}}정답")
        lines.append(f"Dialogue: 3,{ass_time(r0)},{ass_time(card1)},Ans,,0,0,0,,"
                     f"{{\\an5\\pos(540,1040)\\fscx30\\fscy30\\t(0,200,\\fscx115\\fscy115)\\t(200,320,\\fscx100\\fscy100)"
                     f"\\fad(0,200)}}{ass_escape(q['a'])}")
        hide.add(at + 2)      # "정답은, ○○!" 자막 대신 큰 정답
        low[at + 3] = 1340    # 풀이 자막은 정답 아래로
    return lines, hide, low


def build_ass(title: str, timeline, total: float, label: str = "", quiz: list[dict] | None = None) -> str:
    """timeline: [(sentence_start, sentence_end, words)] — words는 문장 기준 상대시간.
    상단 제목: 은은한 위쪽 그림자 + 금색 카테고리 라벨 + 짧은 강조선 + 부드러운 그림자 제목 (박스 없이)."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT_NAME},100,&H0055D7FF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,8,5,5,80,80,0,1
Style: Title,{FONT_NAME},70,&H00FFFFFF,&H00FFFFFF,&H64000000,&H00000000,0,0,0,0,100,100,-1,0,1,3,0,8,100,100,0,1
Style: Label,{FONT_NAME},34,&H0055D7FF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,6,0,1,1,0,8,100,100,0,1
Style: Shape,{FONT_NAME},10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Card,{FONT_NAME},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,7,4,8,70,70,0,1
Style: QNum,{FONT_NAME},60,&H0055D7FF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,4,0,1,5,2,8,90,90,0,1
Style: Count,{FONT_NAME},300,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,12,6,5,40,40,0,1
Style: Ans,{FONT_NAME},140,&H0055D7FF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,10,6,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        t0, t1, fad = ass_time(0), ass_time(total), "\\fad(400,300)"
        # 위쪽만 살짝 어둡게 (밝은 배경에서도 제목이 읽히게)
        lines.append(f"Dialogue: 0,{t0},{t1},Shape,,0,0,0,,{{\\an7\\pos(0,0)\\1a&H70&\\blur90{fad}\\p1}}"
                     f"m -100 -100 l {W + 100} -100 {W + 100} 560 -100 560{{\\p0}}")
        y = 250
        if label:
            lines.append(f"Dialogue: 2,{t0},{t1},Label,,0,0,0,,{{\\an8\\pos(540,{y}){fad}}}{ass_escape(label)}")
            y += 56
        lines.append(f"Dialogue: 2,{t0},{t1},Shape,,0,0,0,,{{\\an8\\pos(540,{y})\\1c&H55D7FF&{fad}\\p1}}"
                     "m 0 0 l 56 0 56 5 0 5{\\p0}")  # 금색 짧은 강조선
        y += 30
        lines.append(f"Dialogue: 2,{t0},{t1},Title,,0,0,0,,{{\\an8\\move(540,{y - 14},540,{y},0,450)\\blur2{fad}}}"
                     f"{ass_escape(title)}")
    hide, low = set(), {}
    if quiz:
        qlines, hide, low = quiz_events(quiz, timeline)
        lines += qlines
    for si, (s_start, s_end, words) in enumerate(timeline):
        if si in hide:
            continue
        chunks = chunk_words(words)
        for ci, chunk in enumerate(chunks):
            c_start = s_start + chunk[0][0]
            c_end = s_start + chunks[ci + 1][0][0] if ci + 1 < len(chunks) else s_end - 0.05
            parts = []
            for wi, (w_start, w_dur, text) in enumerate(chunk):
                nxt = chunk[wi + 1][0] if wi + 1 < len(chunk) else w_start + w_dur
                k = max(1, round((nxt - w_start) * 100))
                parts.append(f"{{\\k{k}}}{ass_escape(text)}")
            anim = f"{{\\an5\\pos(540,{low.get(si, 1080)})\\fad(60,40)\\fscx82\\fscy82\\t(0,140,\\fscx100\\fscy100)}}"
            lines.append(f"Dialogue: 0,{ass_time(c_start)},{ass_time(c_end)},Cap,,0,0,0,,{anim}{' '.join(parts)}")
    return head + "\n".join(lines) + "\n"


# ---------- 배경 ----------
def make_bg_clip(src: str | None, length: float, out: Path, idx: int):
    """1080x1920, 약간 확대 후 천천히 패닝 + 어둡게 + 비네팅."""
    # 시네마틱 톤: 살짝 S커브 대비 + 따뜻한 하이라이트/차가운 그림자 + 은은한 필름 그레인 + 비네팅
    look = ("eq=brightness=-0.03:saturation=1.12:contrast=1.06,"
            "curves=all='0/0.02 0.25/0.21 0.5/0.5 0.75/0.79 1/0.98',"
            "colorbalance=rs=-0.03:bs=0.04:rh=0.04:bh=-0.03,"
            "vignette=PI/5,noise=alls=4:allf=t,fps=30,format=yuv420p")
    if src:
        sx, sy = int(W * 1.12) // 2 * 2, int(H * 1.12) // 2 * 2
        base = f"scale={sx}:{sy}:force_original_aspect_ratio=increase:flags=lanczos,crop={sx}:{sy}"
        if idx % 3 == 2:
            # 천천히 줌인 (Ken Burns)
            z = f"(1+0.10*t/{length:.3f})"
            vf = (f"{base},scale=w='trunc({sx}*{z}/2)*2':h='trunc({sy}*{z}/2)*2':eval=frame:flags=bicubic,"
                  f"crop={W}:{H},{look}")
        else:
            dx = f"(iw-{W})*t/{length:.3f}" if idx % 3 == 0 else f"(iw-{W})*(1-t/{length:.3f})"
            vf = f"{base},crop={W}:{H}:x='{dx}':y='(ih-{H})/2',{look}"
        cmd = [FFMPEG, "-y", "-stream_loop", "-1", "-i", src, "-t", f"{length:.3f}", "-an", "-vf", vf]
    else:
        c0, c1 = GRADIENTS[idx % len(GRADIENTS)]
        cmd = [FFMPEG, "-y", "-f", "lavfi", "-i",
               f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}:speed=0.015:r={FPS}:d={length:.3f}",
               "-vf", look]
    run(cmd + ["-c:v", "libx264", "-preset", "fast", "-crf", "15", "-r", str(FPS), str(out)])


def clip_luma(path: str) -> float:
    """영상 중간 프레임 몇 장의 평균 밝기(0~255). 너무 어두운 클립을 걸러내는 용도."""
    r = subprocess.run([FFMPEG, "-ss", "1", "-i", path, "-t", "4", "-vf", "fps=1,scale=64:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-f", "null", "-"], capture_output=True, text=True, errors="ignore")
    vals = [float(v) for v in re.findall(r"YAVG=([\d.]+)", r.stderr)]
    return sum(vals) / len(vals) if vals else 128.0


def fetch_good_clip(query: str, used: set, min_sec: float, tries: int = 4) -> str | None:
    """너무 어두운(거의 검은) 클립은 건너뛰고 다음 후보 사용."""
    fallback = None
    for _ in range(tries):
        src = fetch_clip(query, used, min_sec=min_sec)
        if not src:
            break
        if clip_luma(src) >= 45:
            return src
        fallback = fallback or src
    return fallback


def join_with_xfade(clips: list[Path], durs: list[float], out: Path):
    if len(clips) == 1:
        shutil.copy(clips[0], out)
        return
    inputs = sum((["-i", str(c)] for c in clips), [])
    graph, prev, offset = [], "[0:v]", 0.0
    for i in range(1, len(clips)):
        offset += durs[i - 1]
        tag = f"[x{i}]"
        graph.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}{tag}")
        prev = tag
    run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(graph), "-map", prev,
         "-c:v", "libx264", "-preset", "fast", "-crf", "15", "-pix_fmt", "yuv420p", str(out)])


def pick_bgm() -> Path | None:
    if not BGM_DIR.exists():
        return None
    files = [p for p in BGM_DIR.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".aac")]
    return random.choice(files) if files else None


# ---------- 내레이션 ----------
def narrate_edge(sentences, voice, tmp: Path):
    """edge-tts 문장별 합성 → tmp/voice.wav. 반환 (timeline, durs, total)."""
    timeline, wavs, durs, t = [], [], [], 0.0
    for i, sent in enumerate(sentences):
        mp3, wav = tmp / f"{i}.mp3", tmp / f"{i}.wav"
        words = attach_punctuation(sent, asyncio.run(_tts(sent, voice, mp3)))
        dur = media_duration(mp3) + PAUSE_SEC
        run([FFMPEG, "-y", "-i", str(mp3), "-af", "apad", "-t", f"{dur:.3f}", "-ar", "44100", "-ac", "2", str(wav)])
        timeline.append((t, t + dur, words))
        wavs.append(wav)
        durs.append(dur)
        t += dur
    lst = tmp / "a.txt"
    lst.write_text("".join(f"file '{w.as_posix()}'\n" for w in wavs), encoding="utf-8")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp / "voice.wav")])
    return timeline, durs, t


def narrate_gemini(sentences, tmp: Path, tone: str | None = None):
    """Gemini TTS로 대본 전체 낭독 → 문장 경계는 앞뒤 문장 사이 쉼의 중간."""
    from voice_gemini import narrate
    raw = tmp / "gemini.wav"
    timed, _ = narrate(sentences, raw, FFMPEG, tone)
    total = media_duration(raw) + 0.5
    run([FFMPEG, "-y", "-i", str(raw), "-af", "apad", "-t", f"{total:.3f}", "-ar", "44100", "-ac", "2",
         str(tmp / "voice.wav")])
    bounds = [0.0]
    for i in range(len(timed) - 1):
        bounds.append((timed[i][-1][1] + timed[i + 1][0][0]) / 2)
    bounds.append(total)
    timeline, durs = [], []
    for i, words in enumerate(timed):
        s0, s1 = bounds[i], bounds[i + 1]
        timeline.append((s0, s1, [(st - s0, en - st, w) for st, en, w in words]))
        durs.append(s1 - s0)
    return timeline, durs, total


# ---------- 퀴즈 ----------
def insert_gaps(tmp: Path, timeline, durs, total, gaps: dict[int, float]):
    """gaps {문장 idx: 초} — 그 문장 뒤에 무음을 끼워 넣고(카운트다운 시간) 이후 타이밍을 뒤로 민다."""
    graph, segs, t0 = [], [], 0.0
    for k, i in enumerate(sorted(gaps)):
        t1 = timeline[i][1]
        graph += [f"[0:a]atrim=start={t0:.3f}:end={t1:.3f},asetpts=PTS-STARTPTS[v{k}]",
                  f"aevalsrc=0|0:c=stereo:s=44100:d={gaps[i]}[z{k}]"]
        segs += [f"[v{k}]", f"[z{k}]"]
        t0 = t1
    graph.append(f"[0:a]atrim=start={t0:.3f},asetpts=PTS-STARTPTS[vend]")
    segs.append("[vend]")
    graph.append("".join(segs) + f"concat=n={len(segs)}:v=0:a=1[a]")
    run([FFMPEG, "-y", "-i", str(tmp / "voice.wav"), "-filter_complex", ";".join(graph), "-map", "[a]",
         "-ar", "44100", "-ac", "2", str(tmp / "voice2.wav")])
    (tmp / "voice2.wav").replace(tmp / "voice.wav")
    shifted, off = [], 0.0
    for i, (s0, s1, words) in enumerate(timeline):
        shifted.append((s0 + off, s1 + off, words))
        off += gaps.get(i, 0)
    return shifted, [d + gaps.get(i, 0) for i, d in enumerate(durs)], total + sum(gaps.values())


def make_sfx(ticks: list[float], dings: list[float], total: float, out: Path):
    """카운트다운 째깍 + 정답 딩동 효과음 트랙 (합성음이라 저작권 걱정 없음)."""
    srcs = [("aevalsrc='0.3*sin(2*PI*1500*t)*exp(-35*t)':s=44100:d=0.15", ticks),
            ("aevalsrc='0.32*sin(2*PI*if(lt(t,0.13),880,1320)*t)*exp(-4*t)':s=44100:d=0.9", dings)]
    graph, mix = [], ""
    for si, (src, times) in enumerate(srcs):
        outs = "".join(f"[s{si}_{k}]" for k in range(len(times)))
        graph.append(f"{src},aformat=channel_layouts=stereo,asplit={len(times)}{outs}")
        for k, t in enumerate(times):
            ms = round(t * 1000)
            graph.append(f"[s{si}_{k}]adelay={ms}|{ms}[d{si}_{k}]")
            mix += f"[d{si}_{k}]"
    n = len(ticks) + len(dings)
    # 길이 제한을 두 겹으로 (무한 apad는 디스크를 다 채운 적 있음)
    graph.append(f"{mix}amix=inputs={n}:normalize=0,apad=whole_dur={total:.3f}[a]")
    run([FFMPEG, "-y", "-filter_complex", ";".join(graph), "-map", "[a]", "-ar", "44100", "-t", f"{total:.3f}", str(out)])


# ---------- 메인 ----------
def make_shorts(script: str, out_path, title: str = "", voice: str = DEFAULT_VOICE,
                scenes: list[str] | None = None, tone: str | None = None, label: str = "",
                quiz: list[dict] | None = None) -> Path:
    """quiz: [{n, q, a, at}] — at번째 문장이 문제, 다음이 뜸·정답·풀이 (autopilot.quiz_script)."""
    sentences = split_sentences(script)
    if quiz and len(sentences) != len(script.splitlines()):
        raise ValueError("퀴즈 대본 문장 안에 문장부호+띄어쓰기가 있어 문장 수가 어긋남")
    out_path = Path(out_path).resolve()
    if not scenes:
        scenes = random.sample(FALLBACK_SCENES, k=min(len(FALLBACK_SCENES), len(sentences)))
    scenes = [scenes[i % len(scenes)] for i in range(len(sentences))]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1) 음성 + 단어 타이밍 (Gemini 사람 목소리 우선, 안 되면 edge-tts)
        timeline = None
        report("voice", "녹음실 준비 완료! 오늘 낭독할 성우를 뽑을게요 🎙", "pd", stage="voice")
        if os.environ.get("TTS", "gemini") == "gemini":
            try:
                timeline, durs, total = narrate_gemini(sentences, tmp, tone)
            except Exception as e:
                print(f"  Gemini TTS 사용 불가 → edge-tts로 대체 ({str(e)[:200]})")
                report("voice", "성우 섭외 실패… 기본 음성(edge-tts)으로 대신 녹음해요", "pd", kind="error")
        if timeline is None:
            timeline, durs, total = narrate_edge(sentences, voice, tmp)
        if quiz:  # 뜸 문장 뒤에 카운트다운 무음 → 카운트다운 시작·정답 공개 시각 계산
            timeline, durs, total = insert_gaps(tmp, timeline, durs, total, {q["at"] + 1: COUNTDOWN for q in quiz})
            for q in quiz:
                q["count_at"] = timeline[q["at"] + 1][1]
                s0, _, words = timeline[q["at"] + 2]   # "정답은, ○○!" — 두 번째 단어(정답)가 들릴 때 공개
                q["reveal_at"] = s0 + (words[1][0] if len(words) > 1 else words[0][0] if words else 0)
            make_sfx([q["count_at"] + k for q in quiz for k in range(COUNTDOWN)], [q["reveal_at"] for q in quiz],
                     total, tmp / "sfx.wav")

        # 2) 배경 영상 — 이어지는 문장이 같은 장면이면 한 클립으로 (컷이 덜 산만하게)
        groups = []
        for i, q in enumerate(scenes):
            if groups and groups[-1][0] == q:
                groups[-1][1].append(i)
            else:
                groups.append((q, [i]))
        gdurs = [sum(durs[i] for i in idx) for _, idx in groups]
        used, clips = set(), []
        report("footage", f"배경 영상 {len(groups)}장면 찾으러 갑니다!", "pixabay", stage="footage", progress=0)
        for gi, (q, idx) in enumerate(groups):
            length = gdurs[gi] + (XFADE if gi < len(groups) - 1 else 0)
            src = fetch_good_clip(q, used, min_sec=length)
            print(f"  [{gi + 1}/{len(groups)}] {gdurs[gi]:.1f}s  {' / '.join(sentences[i] for i in idx)}   "
                  f"🎬 {q if src else '그라데이션'}")
            report("footage", f"[{gi + 1}/{len(groups)}] '{q}' " + ("장면 확보!" if src else "못 찾아서 그라데이션으로 대체"),
                   "pixabay", progress=round((gi + 1) / len(groups), 2))
            clip = tmp / f"bg{gi}.mp4"
            make_bg_clip(src, length, clip, gi)
            clips.append(clip)
        report("edit", "장면 이어 붙이는 중 (부드러운 전환 효과)", "ffmpeg", stage="edit")
        join_with_xfade(clips, gdurs, tmp / "bg.mp4")
        report("edit", "자막 입히고 최종 렌더링 들어갑니다 🎞", "ffmpeg")

        # 3) 자막 + 음악(+ 퀴즈 효과음) 합성
        (tmp / "subs.ass").write_text(build_ass(title, timeline, total, label, quiz), encoding="utf-8")
        (tmp / "fonts").mkdir()
        shutil.copy(FONT_FILE, tmp / "fonts" / FONT_FILE.name)
        bgm = pick_bgm()
        cmd = [FFMPEG, "-y", "-i", "bg.mp4", "-i", "voice.wav"]
        vgraph = "[0:v]ass=subs.ass:fontsdir=fonts[v]"
        pre, mix = "", ["[1:a]"]
        if bgm:
            cmd += ["-stream_loop", "-1", "-i", str(bgm)]
            pre = f"[2:a]volume={BGM_VOLUME},afade=t=in:d=1,afade=t=out:st={max(0, total - 2):.2f}:d=2[m];"
            mix.append("[m]")
            print(f"  🎵 BGM: {bgm.name}")
        if quiz:
            cmd += ["-i", "sfx.wav"]
            mix.append(f"[{len(mix) + 1}:a]")
        agraph = pre + "".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[a]" if len(mix) > 1 \
            else "[1:a]anull[a]"
        cmd += ["-filter_complex", f"{vgraph};{agraph}", "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-tune", "film", "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out_path)]
        run(cmd, cwd=tmp)
    print(f"영상 생성 완료: {out_path} ({media_duration(out_path):.1f}초)")
    report("edit", f"영상 완성! {media_duration(out_path):.1f}초짜리 쇼츠 나왔습니다", "ffmpeg")
    return out_path
