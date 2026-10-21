"""R7-J01 — 复刻 audit semantic-probes.json 的 24 项纯 JS 语义探针。

跑项目自身的 js.Host 执行每项 code，与 audit 里 Node/V8 的 reference
预期比较，输出逐项 match/fail。用途：
  * 定位 13 项缺口里哪些**已修好**、哪些**仍 fail**（真缺口）；
  * 修完每项后回归确认转 match；
  * 不冒充通过——逐项打印 actual vs expected，fail 项明示。

运行：python tools/semantic_probe_check.py   （需完整 3.11 环境）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.scripting import js


def run_code(code: str) -> dict:
    """跑单段 code，返回 {"logs": [...], "error": str|None}（与 audit 同构）。"""
    root = dom.parse(b"<html><body></body></html>")
    logs: list = []

    def _log(s):
        logs.append(str(s))

    host = js.Host(root, log=_log)
    err = None
    try:
        host.execute(code, gated=False)
    except Exception as e:  # noqa: BLENT0
        err = f"{type(e).__name__}: {e}"
    return {"logs": logs, "error": err}


def main() -> int:
    probes = json.load(open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "assessments", "20261018-round7", "semantic-probes.json"),
        encoding="utf-8"))
    matched = 0
    fails = []
    for c in probes["cases"]:
        ref_logs = c["reference"]["logs"]
        ref_err = c["reference"]["error"]
        act = run_code(c["code"])
        ok = (act["logs"] == ref_logs) and (
            (act["error"] is None and ref_err is None)
            or (act["error"] is not None and ref_err is not None
                and "ReferenceError" in act["error"]
                and "ReferenceError" in ref_err))
        # audit 的 reference error 都是 None（Node 全成功）；项目若抛
        # ReferenceError/SyntaxError 而 reference 无错 → 不匹配。
        if ref_err is None and act["error"] is not None:
            ok = False
        if ok:
            matched += 1
        else:
            fails.append(c["id"])
        flag = "OK " if ok else "FAIL"
        if not ok:
            print(f"{flag} {c['id']}\n   expected logs={ref_logs} err={ref_err}\n"
                  f"   actual   logs={act['logs']} err={act['error']}")
    print(f"\n== {matched}/{len(probes['cases'])} matched "
          f"(audit baseline: {probes['matched']}/{probes['total']}) ==")
    print("remaining fails:", fails if fails else "(none)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
