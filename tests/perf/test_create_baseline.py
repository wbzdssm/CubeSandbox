"""Create sandbox baseline."""

METRICS = ("avg", "min", "p95", "max")   # which stats to show

REPORT = {
    "method_en": "Create Sandbox",
    "method_zh": "创建沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
}

LEVELS = (1,)                            # only concurrency=1

import argparse, time, os

ap = argparse.ArgumentParser()
ap.add_argument("-c", type=int, default=1)
ap.add_argument("-n", type=int, default=5)
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("--no-header", action="store_true")
args = ap.parse_args()

from cubesandbox import Sandbox, Config

cfg = Config(
    api_url=os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000"),
    template_id=os.environ.get("CUBE_TEMPLATE_ID", ""),
)

for i in range(args.n):
    t0 = time.time()
    sb = Sandbox.create(config=cfg)
    dt = (time.time() - t0) * 1000
    sb.kill()
    print(f"c={args.c}, {i + 1}/{args.n}, {dt:.0f} ms")
