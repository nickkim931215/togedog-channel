# 투게독 쇼츠 자동화 (togedog-channel) — 작업 노트

투게독 유튜브 채널에 강아지 쇼츠를 **하루 3편** 자동으로 만들어 예약 공개하는 프로그램. 게임 투게독(https://togedog.app)을 알리는 채널.
어느 컴퓨터에서든 이 파일로 맥락을 이어간다. 큰 결정 · 완료한 일이 생기면 여기도 고친다.

> ⚠️ **이 저장소는 GitHub Public(공개)** 이다. 이 파일 · 코드 · 커밋 메시지에 비밀번호 · 토큰 · API 키 · 계정 내부 번호 · 결제 정보를 절대 쓰지 말 것.
> 비밀값은 이 파일에 적지 않음 — 로컬 `.env` · `client_secret.json` · `token_togedog.json`(git 무시, 노트북에만) / GitHub Secrets 에 있음.
> 공개로 두는 이유: 공개 저장소는 Actions 무료 시간 제한이 없어서(사장님 11/14 결정). 나머지 nickkim931215 저장소는 모두 비공개.

## 한눈에
- 실행: GitHub Actions(`nickkim931215/togedog-channel`)가 전부 자동. 사람 손은 문제 생길 때만(디스코드 알림).
- 캄모니(youtube-auto) 코드를 복사해 고친 것. **캄모니와 완전 분리** — 캄모니 저장소 · Secrets · 구글 클라우드 프로젝트 · 실행 시각은 절대 건드리지 않는다(코드는 읽어서 복사만).
- 유튜브 API 는 투게독 로그인용 구글 클라우드 프로젝트("Timer")에 데스크톱 클라이언트를 추가해서 씀 — 그 프로젝트의 **웹 OAuth 클라이언트(투게독 게임 로그인)는 절대 건드리지 말 것.**
- 계정(이름만): GitHub · 구글(유튜브 채널 "투게독") = nickkim931215 계정. 알림 = 디스코드 봇 "투게독 알림" → 사장님 DM (메시지 앞에 [투게독]).

## 기술 스택 · 파일
Python 3.12(Actions) · ffmpeg(BtbN 빌드, libass) · Gemini(대본 + TTS 목소리) · faster-whisper(단어 타이밍 → 자막) · Pixabay(강아지 배경 영상) · Playwright Chromium(게임 화면 촬영) · YouTube Data API · edge-tts(Gemini 목소리 실패 시 대체).

| 파일 | 하는 일 |
|---|---|
| `autopilot.py` | 주제 → 대본 → 영상 → 예약 업로드 → `history.json` 기록. 카테고리 · 시간대 · 공개 시각 · `PLAY_READY` 설정이 여기 |
| `script_writer.py` | Gemini 대본(모델이 404면 `discover_models()`로 대체 모델 찾기) |
| `voice_gemini.py` | Gemini TTS 목소리 4종(Kore · Aoede · Leda · Puck) + Whisper 정렬 + 끝 잡음 자르기 |
| `shorts_maker.py` | 강아지 쇼츠 편집(배경 영상 · 자막 · 음성) |
| `stock_video.py` | Pixabay 배경 영상 검색(검색어 첫 단어 dog) |
| `play_maker.py` | 🎮 플레이 영상: togedog.app 촬영 모드를 로봇이 조작해 찍고 내레이션 · 자막 · 게임 BGM · 마무리 QR |
| `vclock.js` | 가상 시계(그래픽 카드 없는 클라우드에서도 1장 = 1/30초로 매끄럽게 찍기) |
| `healthcheck.py` · `alert.py` | 자가 점검 → 문제 있을 때만 디스코드 DM, 일요일 주간 보고 |
| `auth_login.py` | 유튜브 로그인(한 번만) → `token_togedog.json` |
| `office.py` · `youtube_uploader.py` · `settings.py` | 진행 보고 · 업로드 · `.env` 로더 |
| `bgm/game.mp3` · `assets/qr.png` · `fonts/` | 플레이 영상 음악(게임 BGM) · togedog.app QR · Pretendard 글꼴 |
| `history.json` | 올린 영상 기록(Actions 가 매번 커밋) — 이걸로 빈 회차를 판단하니 손으로 지우지 말 것 |

## 일정 (모두 KST)
- 공개 시각: **06:30(morning) · 18:00(evening) · 21:00(night)** — 사장님 11/09 결정.
- 18시 칸은 **이틀에 한 번 플레이 영상**(`is_play_slot`: 날짜 ordinal 짝수인 날, 예 11/12 · 11/14 · 11/16 …), 나머지는 전부 강아지 쇼츠.
- 강아지 카테고리: breed 견종 상식 · walk_tips 산책 꿀팁 · behavior 행동의 속뜻 · cute 댕댕이 공감 (시간대별 비중, 직전과 같은 카테고리 피함).
- `autopilot.yml`: 매일 20:37 · 00:37 · 04:37 · 10:37(:37분 = 캄모니와 다른 분). 앞으로 26시간 안에 공개될 빈 회차만 만들어 예약 업로드, 다 차 있으면 1분 안에 끝남. 플레이 영상 1편 제작 약 20분.
- `health.yml`: 매일 11:20 예정(실제로는 GitHub 예약 지연으로 몇 시간 늦게 돎 — 11/10~14 모두 18~19시경 성공). 일요일엔 주간 보고 + 시험 제작(dry_run) 1편.

## 실행 방법
- 수동 실행: https://github.com/nickkim931215/togedog-channel/actions/workflows/autopilot.yml → Run workflow (now · category · topic · privacy · dry_run · play_test). 플레이 영상 시험은 play_test=true → 결과는 아티팩트.
- 로컬:
  ```
  python -m venv .venv && .venv\Scripts\activate
  pip install -r requirements.txt && python -m playwright install chromium
  python autopilot.py --no-upload                 # 1편 시험 제작(업로드 안 함)
  python autopilot.py --plan                      # 만들 회차 수
  python play_maker.py --seed 42 --out output/play.mp4   # 플레이 영상 (--reuse 찍어 둔 장면으로 편집만, --no-voice)
  python healthcheck.py                           # 점검
  ```
  로컬엔 `.env`(GEMINI_API_KEY · PIXABAY_API_KEY · DISCORD_BOT_TOKEN) + `client_secret.json` + `token_togedog.json` 필요 — git 에 없으니 노트북에서 직접 옮기거나 `auth_login.py` 로 다시 로그인. Windows 에선 `python -X utf8`.
- 비밀값 바꾸기 = GitHub Secrets 5개: YT_CLIENT_SECRET · YT_TOKEN_TOGEDOG · GEMINI_API_KEY · PIXABAY_API_KEY · DISCORD_BOT_TOKEN (https://github.com/nickkim931215/togedog-channel/settings/secrets/actions).
- **작업 전 반드시 `git pull`** — Actions 가 history.json 을 하루 여러 번 커밋함(안 하면 push 충돌).

## 규칙 · 사장님 결정
- **QR · 투게독 웹앱 홍보는 플레이 영상에만.** 강아지 쇼츠엔 QR · 앱 홍보 넣지 말 것. 단 강아지 쇼츠 설명란 맨 아래 "투게독 → togedog.app" 한 줄은 그대로 둔다.
- 플레이 영상: 마무리 화면에 QR + "프로필에 링크 있어요!"(QR 찍을 시간 1.5초 더). 내레이션은 찍힌 장면 순서대로 Gemini 대본(장면당 1문장, 마지막 '투게독' 권유) → 문장마다 그 장면 시작에 맞춰 놓기. 실패하면 자막 + 음악만.
- 플레이 영상엔 **실제 유저가 안 나옴** — 촬영 모드 연출 캐릭터만(내 화면에만 보임). 장면 = walk · talk · photo · futsal · river (auto 는 빈 화면, dogpark 는 강아지가 화면 밖이라 뺌). 한산해도 최소 8명.
- 자막 글꼴(Pretendard)엔 이모지가 없어 네모로 나옴 → 자막에선 이모지 빼기(제목 · 설명엔 OK).
- 채널 내용: 다정한 존댓말, 수의학 진단 단정 금지, 안전 정보는 정확히, 정치 · 종교 · 자극 금지. 목소리는 Gemini 4종만(ElevenLabs 는 캄모니 전용).
- `requirements.txt` 버전 고정 — 업데이트로 두 번 고장남. 올릴 땐 dry_run 으로 확인 후.
- 유튜브 토큰 권한이 업로드 + 읽기뿐 → 이미 올린 영상 공개 설정 · 설명 수정은 API 로 못 함, 사장님이 유튜브 스튜디오에서 직접(https://studio.youtube.com/).

## 함정 (실제로 겪은 것)
- Gemini 3.8 TTS 는 "말투:" 같은 지시문을 소리 내어 읽음 → 대본만 보내고, 들린 글자가 대본의 112% 넘으면 다시 녹음.
- 같은 TTS 가 말 끝 뒤 무음 다음 0.2초 최대 음량 잡음을 붙임 → `voice_gemini._trim_tail` 로 자름.
- 새 Gemini 키는 2.5 계열 모델이 404 → 모델 목록 + `discover_models()` 대체.
- 이모지 글꼴은 apt `fonts-noto-color-emoji`(예전 github 주소 404), apt 는 가끔 멈춰서 3분 제한.
- 아티팩트 저장 한도는 nickkim931215 계정 전체 공용 → 다른 저장소(togedog 백업 등)가 한도를 넘기면 여기 아티팩트도 실패할 수 있음(2026-11 백업 실패 원인).
- bash `tr` 로 비밀값 다듬지 말 것(특수문자 지움) → 비밀값 정리는 python 으로.

## 현재 상태 · 다음 할 일 (2026-11-14)
- 예약 업로드 정상: 11/09 시작, 매일 3편, 11/15 까지 예약 채워짐. 플레이 영상 11/12 · 11/14 18:00 정상 업로드.
- 대기: 게임 BGM(bgm/game.mp3) 유튜브 사용 저작권 — 사장님 답 대기. 안 되면 무료 음악으로 교체.
- 검토: health 가 예약보다 몇 시간 늦게 도는 것 — 필요하면 cron 분 바꾸기 제안(캄모니와 같은 분 피하기).
- 아이디어(사장님이 원하면): 플레이 영상 장면 추가, 강아지 쇼츠 품질 개선.

## 사장님 선호 · "투게독 불러와" 루틴
- 사장님(비개발자)께 한국어 · 쉬운 말 · 결과 위주. 직접 할 일은 **항상 바로 가는 링크**와 1·2·3 단계로, 한 번만 보이는 값(키 · 토큰)은 **그 단계 전에 미리** "닫지 말고 복사해 두세요" 경고.
- "투게독 불러와" → togedog · togedog-channel 둘 다 `git pull` → 게임 상태 + 쇼츠 상태(history.json 최근 회차, `gh run list -R nickkim931215/togedog-channel -L 5`)를 표로 먼저 요약 → 남은 일 · 사장님 할 일 → "이것부터 시작할게요". 게임 쪽 자세한 건 togedog 저장소 CLAUDE.md.
- 디스코드 알림에 "Claude에게 '투게독 불러와' 하고 알려주세요"가 오면: 링크된 Actions 실행 로그부터 확인.
