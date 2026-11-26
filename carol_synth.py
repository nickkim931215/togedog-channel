"""🎄 게임 속 크리스마스 트리 캐롤(투게독 src/lib/carol.ts)을 영상용 WAV 로 똑같이 합성 — 촬영 로봇은 게임 소리를 못 담아서 편집 때 입힌다.
징글벨 + We Wish You a Merry Christmas (저작권 끝난 곡), 오르골 소리 + 약한 메아리."""
import wave
from pathlib import Path

import numpy as np

BASE = 0.46  # 한 박 (초) — carol.ts 와 같음
SR = 44100
N = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def seq(s: str):
    out = []
    for tok in s.split():
        n, d = tok.split(":")
        beats = float(d)
        out.append((-1, beats) if n == "R" else (60 + N[n[0]] + (int(n[1]) - 4) * 12, beats))
    return out


JINGLE = seq("E4:1 E4:1 E4:2 E4:1 E4:1 E4:2 E4:1 G4:1 C4:1.5 D4:.5 E4:4 F4:1 F4:1 F4:1.5 F4:.5 F4:1 E4:1 E4:1 E4:.5 E4:.5 E4:1 D4:1 D4:1 E4:1 D4:2 G4:2 "
             "E4:1 E4:1 E4:2 E4:1 E4:1 E4:2 E4:1 G4:1 C4:1.5 D4:.5 E4:4 F4:1 F4:1 F4:1.5 F4:.5 F4:1 E4:1 E4:1 E4:.5 E4:.5 G4:1 G4:1 F4:1 D4:1 C4:4 R:2")
WISH = seq("G4:1 C5:1 C5:.5 D5:.5 C5:.5 B4:.5 A4:1 A4:1 A4:1 D5:1 D5:.5 E5:.5 D5:.5 C5:.5 B4:1 G4:1 G4:1 E5:1 E5:.5 F5:.5 E5:.5 D5:.5 C5:1 A4:1 G4:.5 G4:.5 A4:1 D5:1 B4:1 C5:3 R:2")
SONG = JINGLE + WISH


def synth(seconds: float, path: Path, start_beat: float = 0.0) -> Path:
    """seconds 길이의 캐롤 WAV (곡이 끝나면 처음부터 반복)."""
    n = int((seconds + 1.5) * SR)
    buf = np.zeros(n)
    t = -start_beat * BASE
    while t < seconds:
        for midi, beats in SONG:
            dur = beats * BASE
            if midi >= 0 and t + dur > 0:
                f = 440.0 * 2 ** ((midi - 69) / 12)
                ring = min(1.6, dur * 0.95 + 0.9)
                tt = np.arange(int(ring * SR)) / SR
                tone = sum(a * np.sin(2 * np.pi * f * m * tt) for m, a in ((1, 1.0), (2.0, 0.34), (3.01, 0.18), (4.2, 0.07)))
                env = np.minimum(1, tt / 0.004) * np.exp(-tt / 0.42)
                # 길게 누르는 음은 조금 더 울리게
                env *= np.where(tt > dur * 0.95, np.exp(-(tt - dur * 0.95) / 0.25), 1.0)
                s0 = int(max(0, t) * SR)
                off = max(0, int(-t * SR))
                seg = (tone * env)[off:]
                seg = seg[: max(0, n - s0)]
                buf[s0 : s0 + len(seg)] += seg * 0.22
            t += dur
            if t >= seconds:
                break
    # 약한 메아리 (오르골이 울리는 느낌: 0.75박 뒤, 되돌림 0.3, 섞임 0.35)
    d = int(BASE * 0.75 * SR)
    wet = np.zeros(n)
    for k in range(1, 5):
        if d * k < n:
            wet[d * k :] += buf[: n - d * k] * (0.3 ** (k - 1)) * 0.35
    mix = buf + wet
    mix = mix[: int(seconds * SR)]
    fade = int(0.8 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix = mix / max(1e-6, np.abs(mix).max()) * 0.8
    pcm = (mix * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


if __name__ == "__main__":
    print(synth(10, Path("output/carol_test.wav")))
