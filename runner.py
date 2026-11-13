# runner.py : 落ちたら5秒後に起動し直す（ALdiscordBot の runner.py を整理したもの）
#   止めるときは Ctrl+C
import subprocess
import sys
import time

while True:
    print("Sakhalinsk を起動中...")
    code = subprocess.call([sys.executable, "main.py"])
    if code == 0:
        break
    print(f"Sakhalinsk が終了しました（{code}）。5秒後に起動し直します...")
    time.sleep(5)
