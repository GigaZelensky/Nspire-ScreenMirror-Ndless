from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from transport import WindowsNavNet
from wire import control, HELLO, acknowledgement

api = WindowsNavNet()
print("Library:", api.library._name, flush=True)
try:
    started = time.monotonic()
    for attempt in range(180):
        ready = api.begin()
        print(f"{time.monotonic() - started:.2f}s discovery={ready}", flush=True)
        if ready:
            reply = api.exchange(control(HELLO, 5731))
            print("HELLO:", acknowledgement(reply, 5731), flush=True)
            api.end()
            break
        time.sleep(0.25)
finally:
    api.close()
