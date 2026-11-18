#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#==============================================================================
# run_tests.py — Mini NPU 虚拟硬件执行平台：自动化验证 + 性能统计 + 报告导出
#
# 工作流程（每个案例）：
#   1. 调用 Verilator 编译生成的仿真可执行文件（真实 RTL 执行）
#   2. 解析其 JSON 输出（含 RTL 实际计算结果 + 模拟周期数 + 宿主机时间）
#   3. 用 golden.py 独立参考模型计算期望值
#   4. 逐元素比对 -> PASS/FAIL，记录错误位置与输入
#   5. 汇总性能数据，严格区分三类指标
#
# 性能三类指标（绝不混淆）：
#   A. sim_cycles      : RTL 仿真的硬件模拟周期数（来自 harness 的时钟计数）
#   B. host_time_ms    : 仿真程序在 CPU (宿主机) 上的真实运行时间（chrono 实测）
#   C. theoretical     : 理论硬件性能估算（基于周期数与目标频率假设，明确标注为估算）
#==============================================================================
import os, sys, json, subprocess, time, argparse, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import golden

ROOT = Path(__file__).resolve().parent.parent
RES  = ROOT / "results"
LOGS = RES / "logs"
REPS = RES / "reports"
RES.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True); REPS.mkdir(exist_ok=True)

# 仿真可执行文件映射
EXE = {
    "mac":       ROOT / "obj_dir_mac/sim_mac",
    "mat2x2":    ROOT / "obj_dir_mat2x2/sim_matmul",
    "mat4x4":    ROOT / "obj_dir_mat4x4/sim_matmul",
    "mat4x2":    ROOT / "obj_dir_mat4x2/sim_matmul",
    "mat4x6":    ROOT / "obj_dir_mat4x6/sim_matmul",
    "mat1x1":    ROOT / "obj_dir_mat1x1/sim_matmul",
    "npu4":      ROOT / "obj_dir_npu4/sim_npu",
}

def run_exe(key, args, timeout=60):
    exe = EXE[key]
    if not exe.exists():
        raise RuntimeError(f"仿真程序不存在: {exe}（请先运行 build.sh）")
    cmd = [str(exe)] + args + ["--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{exe.name} 运行失败 rc={p.returncode}: {p.stderr[:300]}")
    return json.loads(p.stdout)

def cmp_list(actual, expected, label=""):
    """逐元素比对，返回 (ok, 错误列表)"""
    errs = []
    if len(actual) != len(expected):
        errs.append(f"长度不符 actual={len(actual)} expected={len(expected)}")
        return False, errs
    for i,(a,e) in enumerate(zip(actual,expected)):
        if a != e:
            errs.append(f"[{label or 'idx'}{i}] rtl={a} ref={e} 差={a-e}")
    return (len(errs)==0), errs

class Reporter:
    def __init__(self):
        self.cases = []
        self.host_wall_start = time.perf_counter()

    def add(self, name, desc, ok, errs, sim_cycles, host_ms, extra=None):
        self.cases.append({
            "name": name, "desc": desc, "status": "PASS" if ok else "FAIL",
            "errors": errs, "sim_cycles": sim_cycles,
            "host_time_ms": round(host_ms,6), "extra": extra or {}
        })

    def summary(self):
        n_pass = sum(1 for c in self.cases if c["status"]=="PASS")
        n_fail = len(self.cases)-n_pass
        return {"total":len(self.cases),"pass":n_pass,"fail":n_fail,
                "wall_ms": round((time.perf_counter()-self.host_wall_start)*1000,3)}

#------------------------------------------------------------------------------
# 案例 1：INT8 乘法与 INT32 累加（MAC 单笔 + 边界组合）
#------------------------------------------------------------------------------
def case1_mac(rep):
    vectors = [(3,4,0),(-8,127,1000),(127,-128,-5),(-128,-128,2147483647),(0,55,-777)]
    all_ok=True; errs=[]; sim=0; host=0.0; detail=[]
    for a,b,acc in vectors:
        d = run_exe("mac", ["--mode","single","--a",str(a),"--b",str(b),"--acc",str(acc)])
        x = d["transactions"][0]
        ref = golden.golden_mac(a,b,acc)
        ok,e = cmp_list([x["rtl_out"]],[ref],"mac")
        all_ok &= ok; errs += [f"a={a},b={b},acc={acc}: "+x for x in e]
        sim += d["total_sim_cycles"]; host += d["host_time_ms"]
        detail.append({"a":a,"b":b,"acc":acc,"rtl":x["rtl_out"],"ref":ref,"latency":x["latency"]})
    rep.add("案例1","INT8乘法与INT32累加（含边界 127×-128）",all_ok,errs,sim,host,{"vectors":detail})

#------------------------------------------------------------------------------
# 案例 2：2×2 矩阵乘法（题目给定 A=1,2,3,4 B=5,6,7,8 期望 19,22,43,50）
#------------------------------------------------------------------------------
def case2_mat2x2(rep):
    A=[1,2,3,4]; B=[5,6,7,8]; exp=[19,22,43,50]
    d = run_exe("mat2x2", ["--matrix", ",".join(map(str,A+B))])
    rtl = d["tasks"][0]["C_rtl"]
    ok,e = cmp_list(rtl,exp,"C")
    ref = golden.golden_matmul(A,B,2,2)
    ref_ok,_ = cmp_list(rtl,ref,"Cref")
    rep.add("案例2","2×2矩阵乘法（题目给定：期望 19,22,43,50）",ok and ref_ok,e,
            d["total_sim_cycles"],d["host_time_ms"],
            {"A":A,"B":B,"rtl":rtl,"expected":exp,"golden":ref})

#------------------------------------------------------------------------------
# 案例 3：更大尺寸矩阵乘法（4×4 单位阵 + 随机，独立 golden）
#------------------------------------------------------------------------------
def case3_mat4x4(rep):
    A=[1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
    B=[1,2,3,4, 5,6,7,8, 9,10,11,12, 13,14,15,16]
    d = run_exe("mat4x4", ["--matrix", ",".join(map(str,A+B))])
    rtl=d["tasks"][0]["C_rtl"]; ref=golden.golden_matmul(A,B,4,4)
    ok,e=cmp_list(rtl,ref,"C")
    rep.add("案例3","4×4矩阵乘法（单位阵×递增值）",ok,e,
            d["total_sim_cycles"],d["host_time_ms"],{"A":A,"B":B,"rtl":rtl,"golden":ref})

#------------------------------------------------------------------------------
# 案例 4：连续多次计算（NPU 顶层 5 组连续任务）
#------------------------------------------------------------------------------
def case4_stream(rep):
    d = run_exe("npu4", ["--count","5"])
    all_ok=True; errs=[]; details=[]
    for idx,j in enumerate(d["jobs"]):
        ref=golden.golden_matmul(j["A"],j["B"],4,4)
        ok,e=cmp_list(j["C_rtl"],ref,f"job{idx}")
        all_ok&=ok; errs+=e
        details.append({"job":idx,"rtl":j["C_rtl"],"golden":ref,"done_cyc":j["done_cyc"]})
    rep.add("案例4","NPU顶层连续5次矩阵计算",all_ok,errs,
            d["total_sim_cycles"],d["host_time_ms"],{"jobs":details})

#------------------------------------------------------------------------------
# 案例 5：不同参数配置（N=4/K=2, N=4/K=6, N=1/K=1）
#------------------------------------------------------------------------------
def case5_params(rep):
    cfgs=[("mat4x2",4,2),("mat4x6",4,6),("mat1x1",1,1)]
    all_ok=True; errs=[]; sim=0; host=0; details=[]
    for key,N,K in cfgs:
        rng=[((i*37+11)%21)-10 for i in range(N*K)]      # A
        rng2=[((i*13+5)%21)-10 for i in range(K*N)]      # B
        d=run_exe(key,["--matrix",",".join(map(str,rng+rng2))])
        rtl=d["tasks"][0]["C_rtl"]; ref=golden.golden_matmul(rng,rng2,N,K)
        ok,e=cmp_list(rtl,ref,f"N{N}K{K}")
        all_ok&=ok; errs+=e; sim+=d["total_sim_cycles"]; host+=d["host_time_ms"]
        details.append({"N":N,"K":K,"rtl":rtl,"golden":ref,"ok":ok})
    rep.add("案例5","不同参数配置（N4K2/N4K6/N1K1）",all_ok,errs,sim,host,{"configs":details})

#------------------------------------------------------------------------------
# 案例 6：边界数值与溢出（int8 边界 + int32 累加回绕）
#------------------------------------------------------------------------------
def case6_boundary(rep):
    # 4x4: A[0][0]=127,B[0][0]=-128 -> -16256; 其余0
    A=[127]+[0]*15; B=[-128]+[0]*15
    d=run_exe("mat4x4",["--matrix",",".join(map(str,A+B))])
    rtl=d["tasks"][0]["C_rtl"]; ref=golden.golden_matmul(A,B,4,4)
    ok,e=cmp_list(rtl,ref,"C")
    # MAC int32 回绕：acc=INT_MAX, a=127,b=127 -> 溢出按补码截断
    d2=run_exe("mac",["--mode","single","--a","127","--b","127","--acc","2147483647"])
    mo=d2["transactions"][0]["rtl_out"]; mr=golden.golden_mac(127,127,2147483647)
    ok2,e2=cmp_list([mo],[mr],"mac_overflow")
    rep.add("案例6","边界数值与int32累加回绕（127×-128 / INT_MAX溢出）",ok and ok2,e+e2,
            d["total_sim_cycles"]+d2["total_sim_cycles"],d["host_time_ms"]+d2["host_time_ms"],
            {"mat_rtl":rtl,"mat_golden":ref,"mac_overflow_rtl":mo,"mac_overflow_ref":mr})

#------------------------------------------------------------------------------
# 案例 7：复位、握手与流水线（MAC 多模式 + 延迟断言）
#------------------------------------------------------------------------------
def case7_protocol(rep):
    all_ok=True; errs=[]; sim=0; host=0; details=[]
    for mode in ["stream","backpressure","reset","random"]:
        d=run_exe("mac",["--mode",mode,"--count","8"])
        sim+=d["total_sim_cycles"]; host+=d["host_time_ms"]
        # 逐笔对照参考 + 延迟检查（无背压时 latency 应为 3）
        lat_ok=True
        for x in d["transactions"]:
            ref=golden.golden_mac(x["a"],x["b"],x["acc"])
            if x["rtl_out"]!=ref:
                all_ok=False; errs.append(f"{mode}: a={x['a']} b={x['b']} acc={x['acc']} rtl={x['rtl_out']} ref={ref}")
            if mode in ("stream","random") and x["latency"]!=3:
                lat_ok=False
        if not lat_ok:
            all_ok=False; errs.append(f"{mode}: 延迟不等于3流水深度")
        details.append({"mode":mode,"txns":len(d["transactions"]),
                        "latencies":[x["latency"] for x in d["transactions"]],
                        "cycles":d["total_sim_cycles"]})
    rep.add("案例7","复位/握手/背压/流水线延迟（MAC 4种模式）",all_ok,errs,sim,host,{"modes":details})

CASES = [case1_mac,case2_mat2x2,case3_mat4x4,case4_stream,case5_params,case6_boundary,case7_protocol]

#------------------------------------------------------------------------------
# 报告导出
#------------------------------------------------------------------------------
def _build_md(payload):
    """由结果 payload 构建 Markdown 报告行列表"""
    summary=payload["summary"]
    lines=["# Mini NPU 虚拟硬件执行平台 — 测试与性能报告","",
           f"> 生成时间：{payload['generated']}  ",
           f"> 工具：Verilator {payload['verilator_version']}（RTL 真实编译执行）","",
           "## 汇总","",
           f"- 案例总数：**{summary['total']}**　PASS：**{summary['pass']}**　FAIL：**{summary['fail']}**",
           f"- 框架墙钟时间（Python 侧）：{summary['wall_ms']:.3f} ms","",
           "## 逐案例结果","",
           "| 案例 | 描述 | 状态 | 仿真周期(A) | 宿主机时间ms(B) |",
           "|------|------|------|------------|----------------|"]
    for c in payload["cases"]:
        lines.append(f"| {c['name']} | {c['desc']} | {c['status']} | {c['sim_cycles']} | {c['host_time_ms']:.4f} |")
    lines += ["","## 性能口径说明","",
        "- **A 模拟周期数**：RTL 仿真中 clock driver 记录的硬件时钟周期，来自真实 RTL 执行。",
        "- **B 宿主机时间**：C++ 仿真程序在 CPU 上的真实墙钟耗时（std::chrono 实测），**不是硬件速度**。",
        "- **C 理论硬件估算**：若目标频率 F，则理论执行时间≈周期数/F（本报告不代入具体 F，避免误导）。",
        "> 严禁将 B（软件仿真速度）当作真实 FPGA 性能。本平台未接任何 FPGA，无上板实测数据。",""]
    errcases=[c for c in payload["cases"] if c["status"]=="FAIL"]
    if errcases:
        lines += ["## 失败明细（不隐藏）",""]
        for c in errcases:
            lines.append(f"### {c['name']} — {c['desc']}")
            for e in c["errors"][:20]: lines.append(f"- {e}")
            lines.append("")
    return lines

def export_reports(rep, only=None):
    summary=rep.summary()
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    payload={"project":"Mini NPU 虚拟硬件执行平台","generated":stamp,
             "verilator_version":"5.020","summary":summary,"cases":rep.cases}

    jp=REPS/f"test_report_{stamp}.json"
    jp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    mp=REPS/f"test_report_{stamp}.md"
    mp.write_text("\n".join(_build_md(payload)),encoding="utf-8")

    # latest 仅在全量运行时更新，避免 Web 单案例运行覆盖完整结果
    if len(rep.cases) == len(CASES):
        (REPS/"latest_results.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        (REPS/"latest_report.md").write_text("\n".join(_build_md(payload)),encoding="utf-8")
    return summary, jp, mp

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--case",help="只运行指定案例名（如 案例2），默认全部")
    ap.add_argument("--quick",action="store_true",help="快速模式（减少随机数量）")
    args=ap.parse_args()

    print("="*60)
    print(" Mini NPU 虚拟硬件执行平台 — 自动化验证")
    print("="*60)
    rep=Reporter()
    for fn in CASES:
        name={"case1_mac":"案例1","case2_mat2x2":"案例2","case3_mat4x4":"案例3",
              "case4_stream":"案例4","case5_params":"案例5","case6_boundary":"案例6",
              "case7_protocol":"案例7"}[fn.__name__]
        if args.case and args.case!=name: continue
        try:
            fn(rep)
            c=rep.cases[-1]
            print(f" [{c['status']}] {c['name']} {c['desc']}  (cyc={c['sim_cycles']}, host={c['host_time_ms']:.4f}ms)")
            if c["status"]=="FAIL":
                for e in c["errors"][:5]: print("        - "+e)
        except Exception as ex:
            rep.add(name,"执行异常",False,[f"Exception: {ex}"],0,0)
            print(f" [FAIL] {name} 异常: {ex}")

    summary,jp,mp=export_reports(rep)
    print("="*60)
    print(f" 汇总: 总计={summary['total']}  PASS={summary['pass']}  FAIL={summary['fail']}")
    print(f" 报告: {mp.name} / {jp.name}")
    print("="*60)
    return 0 if summary["fail"]==0 else 1

if __name__=="__main__":
    sys.exit(main())
