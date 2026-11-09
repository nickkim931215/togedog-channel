"""Gemini TTS(사람 같은 내레이션) + Whisper 단어 타이밍 정렬.

목소리는 Gemini 4종 랜덤 (투게독은 ElevenLabs 안 씀 — 캄모니 가족 목소리는 캄모니 전용).
대본 전체를 한 번에 읽혀 톤을 일정하게 유지하고, Whisper로 단어별 시간을 뽑아
원문 대본 단어에 글자 단위로 맞춰 붙임 (자막 하이라이트용).
"""
import difflib
import json
import os
import random
import re
import subprocess
import urllib.request
import wave
from pathlib import Path

from settings import load_env

# (음성 이름, 가중치, 목소리 성격) — 말투(톤)는 카테고리별로 따로 받음
VOICES = [
    ("Kore", 3, "정갈하고 또박또박한 여성 아나운서 목소리"),
    ("Aoede", 3, "따뜻하고 포근한 여성 목소리"),
    ("Leda", 2, "밝고 상냥한 젊은 여성 목소리"),
    ("Puck", 2, "경쾌하고 친근한 남성 목소리"),
]
DEFAULT_TONE = "자연스럽고 따뜻하게, 진심을 담아"
# 투게독 키(새 사용자)는 2.5 계열이 404 — 11/09 확인된 모델 먼저
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
_whisper = None


ELEVEN_MODEL = "eleven_multilingual_v2"


def pick_voice(gemini_only: bool = False):
    pool = [v for v in VOICES if not (gemini_only and v[0].startswith("eleven:"))]
    # VOICE 환경변수(예: jennie, Aoede)로 이번 실행 목소리 고정
    want = os.environ.get("VOICE", "").strip().lower()
    forced = [v for v in pool if want and (want in v[0].lower() or want in v[2].lower())]
    if forced:
        return forced[0]
    return random.choices(pool, weights=[v[1] for v in pool])[0]


def _write_pcm(pcm: bytes, out_wav: Path):
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)


def synth_eleven(text: str, voice_id: str, out_wav: Path) -> str:
    """ElevenLabs TTS → 24kHz mono wav. 모델명 반환."""
    load_env()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY 없음")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_24000",
        data=json.dumps({"text": text, "model_id": ELEVEN_MODEL, "language_code": "ko",
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        _write_pcm(r.read(), out_wav)
    return ELEVEN_MODEL


def synth(text: str, voice: str, style: str, out_wav: Path) -> str:
    """Gemini TTS → 24kHz mono wav. 성공한 모델명 반환."""
    from google import genai
    from google.genai import types
    load_env()
    # google-genai 3.x 기본 주소가 v1으로 바뀜 → v1엔 speechConfig가 없어 400. TTS는 v1beta 고정
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(api_version="v1beta", timeout=90_000))
    cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    last = None
    models = list(TTS_MODELS)
    for m in models:
        for _ in range(2):
            try:
                r = client.models.generate_content(model=m, contents=f"{style}:\n{text}" if style else text,
                                                   config=cfg)
                _write_pcm(r.candidates[0].content.parts[0].inline_data.data, out_wav)
                return m
            except Exception as e:
                last = e
                if "404" in str(e)[:20]:  # 종료된 모델 — 재시도 의미 없음
                    break
        if m == models[-1] and len(models) == len(TTS_MODELS) and "404" in str(last)[:20]:
            # 구글이 TTS 모델을 종료함 → 지금 쓸 수 있는 TTS 모델로 대체 (최대 2개)
            from script_writer import discover_models
            models += [n for n in discover_models(client, "tts") if n not in models][:2]
    raise RuntimeError(f"Gemini TTS 실패: {last}")


def _whisper_words(wav: Path, ffmpeg: str):
    """[(start, end, text)] — numpy로 직접 넘김 (PyAV 버전 문제 회피)."""
    global _whisper
    import numpy as np
    from faster_whisper import WhisperModel
    raw = subprocess.run([ffmpeg, "-i", str(wav), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    if _whisper is None:
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segs, _ = _whisper.transcribe(audio, language="ko", word_timestamps=True)
    return [(w.start, w.end, w.word.strip()) for s in segs for w in s.words]


def _chars(s: str) -> str:
    return re.sub(r"[^\w]", "", s)


def align(sentences: list[str], heard) -> tuple[list[list[tuple[float, float, str]]], float]:
    """원문 단어마다 (start, end) 부여. 반환: 문장별 [(start, end, word)] (절대시간), 일치율."""
    # Whisper 쪽 글자별 시간 (단어 안에서 균등 분배)
    h_chars, h_times = [], []
    for st, en, w in heard:
        cs = _chars(w)
        for i, ch in enumerate(cs):
            h_chars.append(ch)
            h_times.append((st + (en - st) * i / len(cs), st + (en - st) * (i + 1) / len(cs)))
    # 원문 글자 → (문장, 단어) 인덱스
    words = [s.split() for s in sentences]
    s_chars, owner = [], []
    for si, ws in enumerate(words):
        for wi, w in enumerate(ws):
            for ch in _chars(w):
                s_chars.append(ch)
                owner.append((si, wi))
    sm = difflib.SequenceMatcher(None, s_chars, h_chars, autojunk=False)
    times = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            o = owner[a + k]
            t0, t1 = h_times[b + k]
            cur = times.get(o)
            times[o] = (min(cur[0], t0), max(cur[1], t1)) if cur else (t0, t1)
    ratio = sm.ratio()
    # 매칭 안 된 단어는 앞뒤 단어 사이로 보간
    flat = [(si, wi) for si, ws in enumerate(words) for wi in range(len(ws))]
    out_flat, prev_end = [], 0.0
    for idx, o in enumerate(flat):
        if o in times:
            st, en = times[o]
            st = max(st, prev_end)
            en = max(en, st + 0.05)
        else:
            nxt = next((times[p][0] for p in flat[idx + 1:] if p in times), prev_end + 0.4)
            st, en = prev_end, max(prev_end + 0.05, min(nxt, prev_end + 0.4))
        out_flat.append((o, st, en))
        prev_end = en
    result = [[] for _ in sentences]
    for (si, wi), st, en in out_flat:
        result[si].append((st, en, words[si][wi]))
    return result, ratio


def narrate(sentences: list[str], out_wav: Path, ffmpeg: str, tone: str | None = None):
    """대본 전체 낭독 + 문장/단어 타이밍. 반환 (문장별 단어 [(start,end,word)], 음성 이름)."""
    text = "\n".join(sentences)
    from office import report
    name, _, character = pick_voice()
    report("voice", "제가 낭독 맡았어요! 녹음 시작합니다", name, voice=name)
    if name.startswith("eleven:"):
        # 뽑힌 ElevenLabs 목소리 → 나머지 ElevenLabs 목소리 순으로 시도, 다 안 되면 Gemini
        tries = [name] + [v[0] for v in VOICES if v[0].startswith("eleven:") and v[0] != name]
        for name in tries:
            try:
                model = synth_eleven(text, name.split(":", 1)[1], out_wav)
                break
            except Exception as e:
                print(f"  ElevenLabs {name} 실패 ({str(e)[:200]})")
                report("voice", "목이 안 좋아서… 다른 성우에게 넘길게요 😷", name, kind="error")
        else:
            print("  ElevenLabs 목소리 모두 실패 → Gemini 목소리로 대체")
            name, _, character = pick_voice(gemini_only=True)
            report("voice", "제가 대신 낭독할게요!", name, voice=name)
    # 3.8 TTS는 말투 지시를 어떤 형식으로 줘도 가끔 소리 내어 읽음(11/09) → 대본만 보내고,
    # 들린 글자가 대본보다 확 많으면(지시문 · 딴말이 섞임) 다시 녹음
    for attempt in range(3):
        model = synth(text, name, "", out_wav)
        heard = _whisper_words(out_wav, ffmpeg)
        extra = len(_chars("".join(w for *_, w in heard))) / max(1, len(_chars(text)))
        if extra <= 1.12:
            break
        print(f"  ⚠ 낭독에 대본에 없는 말이 섞임 (들린 글자 {extra:.0%}) → 다시 녹음")
    else:
        raise RuntimeError("낭독에 대본에 없는 말이 계속 섞임")
    timed, ratio = align(sentences, heard)
    print(f"  🎙 TTS {name} ({model}), 대본 일치율 {ratio:.0%}")
    report("edit", f"싱크 완료! 대본 일치율 {ratio:.0%}", "whisper")
    if ratio < 0.6:
        raise RuntimeError(f"낭독이 대본과 많이 다름 (일치율 {ratio:.0%})")
    return timed, name
