"""대표님 디스코드 DM으로 알림 — 사람 손이 필요할 때·주간 보고만 (표준 라이브러리).

  python alert.py "보낼 내용"
DISCORD_BOT_TOKEN 필요. 받는 사람은 DISCORD_OWNER_ID (기본: 대표님 디스코드 ID).
투게독 알림은 앞에 [투게독]을 붙임 (캄모니 알림과 구분).
"""
import json
import os
import sys
import urllib.request

OWNER = os.environ.get("DISCORD_OWNER_ID") or "1344178521752469606"
API = "https://discord.com/api/v10"


def _call(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_BOT_TOKEN"],
                                          "Content-Type": "application/json",
                                          "User-Agent": "togedog-channel (https://togedog.app, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")


def send(text: str) -> bool:
    if not os.environ.get("DISCORD_BOT_TOKEN"):
        print("(DISCORD_BOT_TOKEN 없음 — 알림 생략)\n" + text)
        return False
    text = ("[투게독] " + text)[:1900]
    try:
        dm = _call("/users/@me/channels", {"recipient_id": OWNER})
        _call(f"/channels/{dm['id']}/messages", {"content": text})
        return True
    except Exception as e:
        print(f"  (DM 실패: {str(e)[:120]})")
    return False


if __name__ == "__main__":
    print("보냄" if send(" ".join(sys.argv[1:])) else "못 보냄")
