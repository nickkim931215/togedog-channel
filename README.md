# 투게독 쇼츠 자동화

투게독 유튜브 채널에 쇼츠를 하루 3편(06:30 · 18:00 · 21:00 KST) 자동으로 만들어 예약 공개합니다.
캄모니(youtube-auto) 코드를 복사해 고친 것이며, 저장소 · 비밀값 · 실행 시각 모두 캄모니와 따로입니다.

- `autopilot.py` — 주제 → 대본(Gemini) → 영상 → 예약 업로드 → `history.json` 기록
- 카테고리: 견종 상식 · 산책 꿀팁 · 행동의 속뜻 · 댕댕이 공감 (18시 칸은 이틀에 한 번 플레이 영상 — 준비 중)
- `healthcheck.py` — 매일 점검, 문제 있을 때만 디스코드 DM ("투게독 알림" 봇)

GitHub Secrets: `YT_CLIENT_SECRET` · `YT_TOKEN_TOGEDOG` · `GEMINI_API_KEY` · `PIXABAY_API_KEY` · `DISCORD_BOT_TOKEN`
