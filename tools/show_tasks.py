import sys
from pathlib import Path

from morkovsk.ozon.game import GameClient

cookie = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
proxy = sys.argv[2] if len(sys.argv) > 2 else None
client = GameClient(cookie, proxy=proxy, on_log=print)
client.warmup()
print("сессия:", client.auth_state(), "| баланс:", client.balance(), "| билеты:", client.main_page().tickets)
for task in client.tasks():
    print(f"  {task.id} [{task.kind}] {'готово' if task.done else 'нет'} +{task.reward} | {task.name[:50]}")
