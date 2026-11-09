"""캄모니의 'AI 일터' 실시간 보고를 대신하는 빈 껍데기 — 투게독은 일터 페이지가 없어 콘솔 출력만.
(캄모니 코드를 그대로 가져와 쓰기 위해 같은 이름의 함수만 둠)"""
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))


def report(*args, **kw):
    pass


def board():
    pass
