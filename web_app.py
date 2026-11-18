#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#==============================================================================
# web_app.py — Mini NPU 虚拟硬件执行平台 Web 控制界面（阶段8）
# 提供 REST API：源码浏览/上传、测试选择与启动、结果查看、报告下载。
# 运行测试时通过子进程真正调用 Verilator 编译的仿真程序（复用 run_tests.py）。
#==============================================================================
import os, sys, json, subprocess, threading, time, datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_file, render_template

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
RES  = ROOT / "results"
REPS = RES / "reports"
RTL  = ROOT / "src" / "rtl"

app = Flask(__name__)
_run_status = {"running": False, "started": None, "output": [], "rc": None}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/cases")
def api_cases():
    cases = [
        {"id":"案例1","name":"INT8乘法与INT32累加","desc":"含边界 127×-128 的 MAC 运算"},
        {"id":"案例2","name":"2×2矩阵乘法","desc":"A=[1,2;3,4] B=[5,6;7,8] 期望 19,22,43,50"},
        {"id":"案例3","name":"4×4矩阵乘法","desc":"单位阵×递增值"},
        {"id":"案例4","name":"NPU顶层连续计算","desc":"连续5次矩阵计算"},
        {"id":"案例5","name":"不同参数配置","desc":"N4K2 / N4K6 / N1K1"},
        {"id":"案例6","name":"边界与溢出","desc":"int8边界 / int32回绕"},
        {"id":"案例7","name":"复位握手流水线","desc":"MAC stream/背压/复位/延迟"},
    ]
    return jsonify(cases)

@app.route("/api/sources")
def api_sources():
    files = {}
    for p in sorted(RTL.glob("*.sv")):
        files[p.name] = p.read_text(encoding="utf-8", errors="replace")
    return jsonify(files)

@app.route("/api/source/<name>", methods=["GET"])
def api_source(name):
    # 防目录穿越
    if "/" in name or "\\" in name or not name.endswith(".sv"):
        return jsonify({"error":"非法文件名"}), 400
    p = RTL / name
    if not p.exists():
        return jsonify({"error":"文件不存在"}), 404
    return jsonify({"name":name, "content":p.read_text(encoding="utf-8",errors="replace")})

@app.route("/api/source/<name>", methods=["POST"])
def api_upload_source(name):
    """上传/覆盖 RTL 源码（会先备份原始版本）"""
    if "/" in name or "\\" in name or not name.endswith(".sv"):
        return jsonify({"error":"非法文件名"}), 400
    p = RTL / name
    content = request.json.get("content","")
    if not content.strip():
        return jsonify({"error":"内容为空"}), 400
    if p.exists():
        bak = RTL / (name + ".web bak".replace(" ","_"))
        bak.write_text(p.read_text(encoding="utf-8",errors="replace"),encoding="utf-8")
    p.write_text(content,encoding="utf-8")
    return jsonify({"ok":True,"name":name,"note":"已保存（原版本已备份为 *.sv_web_bak）"})

@app.route("/api/run", methods=["POST"])
def api_run():
    if _run_status["running"]:
        return jsonify({"error":"已有测试在运行"}), 409
    case = request.json.get("case")  # None=全部
    cmd = [sys.executable, str(ROOT/"scripts"/"run_tests.py")]
    if case: cmd += ["--case", case]
    _run_status.update(running=True, started=datetime.datetime.now().isoformat(),
                       output=[], rc=None)
    def worker():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, cwd=str(ROOT))
            for line in p.stdout:
                _run_status["output"].append(line.rstrip())
            p.wait()
            _run_status["rc"] = p.returncode
        except Exception as e:
            _run_status["output"].append(f"异常: {e}")
            _run_status["rc"] = -1
        finally:
            _run_status["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok":True,"case":case or "全部"})

@app.route("/api/run/status")
def api_run_status():
    out = _run_status["output"][-200:]
    return jsonify({**_run_status, "output":out})

@app.route("/api/build", methods=["POST"])
def api_build():
    """重新构建 RTL（真正调用 build.sh）"""
    try:
        p = subprocess.run(["bash", str(ROOT/"scripts"/"build.sh")],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=600)
        return jsonify({"ok": p.returncode==0, "rc":p.returncode,
                        "output":(p.stdout+p.stderr).splitlines()[-60:]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/results")
def api_results():
    p = REPS/"latest_results.json"
    if not p.exists(): return jsonify({"error":"尚无结果，请先运行测试"})
    return jsonify(json.loads(p.read_text(encoding="utf-8")))

@app.route("/api/report/download")
def api_report_download():
    p = REPS/"latest_report.md"
    if not p.exists(): return jsonify({"error":"尚无报告"}), 404
    return send_file(str(p), as_attachment=True, download_name="npu_test_report.md")

if __name__ == "__main__":
    # 线上发布平台通过 PORT 环境变量注入端口；本地默认 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
