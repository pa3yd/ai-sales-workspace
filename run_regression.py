# -*- coding: utf-8 -*-
"""统一回归运行器（venv 内无 pytest 时的替代方案）。

用法：
    .venv/Scripts/python.exe run_regression.py            # 跑全部 test_*.py
    .venv/Scripts/python.exe run_regression.py r6 r23     # 只跑文件名含关键字的

规则：
  - 文件自带 `if __name__` 入口 -> 直接当脚本跑（尊重其自带断言/打印）
  - 无入口的 pytest 风格文件 -> 用 harness 导入模块并逐个调用 test_* 函数
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

HARNESS = r"""
import importlib.util, json, sys, traceback
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("_tmod", path)
mod = importlib.util.module_from_spec(spec)
sys.modules["_tmod"] = mod
passed, failed = [], []
try:
    spec.loader.exec_module(mod)          # 模块级脚本测试在这里就跑完了
except SystemExit as e:                   # 老式脚本用 sys.exit(0/1) 表达结果
    if e.code not in (0, None):
        failed.append(("<module>", f"SystemExit({e.code})"))
except BaseException as e:
    failed.append(("<module>", "".join(traceback.format_exception_only(type(e), e)).strip()[:400]))
names = sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n)))
for n in names:
    f = getattr(mod, n)
    try:
        if f.__code__.co_argcount:
            continue
        f()
        passed.append(n)
    except SystemExit as e:
        if e.code not in (0, None):
            failed.append((n, f"SystemExit({e.code})"))
        else:
            passed.append(n)
    except BaseException as e:
        failed.append((n, "".join(traceback.format_exception_only(type(e), e)).strip()[:400]))
print("__RESULT__" + json.dumps({"total": len(passed) + len(failed), "passed": len(passed),
                                 "failed": failed}, ensure_ascii=False))
"""


def run_case(path: Path, direct: bool):
    try:
        if direct:
            proc = subprocess.run([PY, str(path)], cwd=str(ROOT), capture_output=True,
                                  text=True, encoding="utf-8", errors="ignore", timeout=600)
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                tail = "\n".join([l for l in out.strip().splitlines() if l.strip()][-6:])
                return False, 0, 0, tail or f"returncode {proc.returncode}"
            m = re.search(r"(\d+)\s*/\s*(\d+)\s*项通过", out)          # 36/36 项通过
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                return a == b, a, b, ""
            m = re.search(r"通过\s*(\d+)\s*[·/]\s*失败\s*(\d+)", out)   # 通过 42 · 失败 1
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                return b == 0, a, a + b, ""
            m = re.search(r"(\d+)\s*通过\s*/\s*(\d+)\s*失败", out)      # 55 通过 / 0 失败
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                return b == 0, a, a + b, ""
            return True, 0, 0, ""
        else:
            proc = subprocess.run([PY, "-c", HARNESS, str(path)], cwd=str(ROOT),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="ignore", timeout=600)
            line = ""
            for l in (proc.stdout or "").splitlines():
                if l.startswith("__RESULT__"):
                    line = l[len("__RESULT__"):].strip()
            if not line:
                tail = "\n".join([l for l in ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()][-6:])
                return False, 0, 0, tail or f"returncode {proc.returncode}"
            data = json.loads(line)
            detail = "; ".join(f"{n}: {e}" for n, e in data["failed"][:6])
            return (not data["failed"]), data["passed"], data["total"], detail
    except subprocess.TimeoutExpired:
        return False, 0, 0, "TIMEOUT(600s)"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    keys = [a.lower() for a in sys.argv[1:]]
    files = sorted(ROOT.glob("test_*.py"))
    if keys:
        files = [f for f in files if any(k in f.name.lower() for k in keys)]
    rows = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        direct = bool(re.search(r"^if __name__", text, re.M))
        ok, a, b, detail = run_case(f, direct)
        rows.append((f.name, ok, a, b, detail))
        flag = "PASS" if ok else "FAIL"
        cnt = f"{a}/{b}" if b else "-"
        print(f"[{flag}] {f.name:<48} {cnt:>8}  {detail[:150] if not ok else ''}", flush=True)
    total = len(rows)
    good = sum(1 for r in rows if r[1])
    print("-" * 70)
    print(f"文件级通过 {good}/{total}")
    bad = [r[0] for r in rows if not r[1]]
    if bad:
        print("失败文件：" + ", ".join(bad))
    return 0 if good == total else 1


if __name__ == "__main__":
    sys.exit(main())
