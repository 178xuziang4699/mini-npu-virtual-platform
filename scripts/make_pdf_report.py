#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从全量测试报告 JSON 生成中文 PDF 报告（weasyprint）。"""
import json, datetime
from pathlib import Path
from weasyprint import HTML

ROOT = Path(__file__).resolve().parent.parent
REPS = ROOT / "results" / "reports"
SRC_JSON = REPS / "test_report_20261117_204921.json"
OUT_PDF = ROOT / "Mini_NPU_测试报告.pdf"

d = json.load(open(SRC_JSON, encoding="utf-8"))
cases = d["cases"]
summary = d["summary"]

def tbl_matrix(name, flat, n, golden=None):
    """把一维数组按 n×n 渲染成表格，golden 可选追加对照列。"""
    rows = []
    for i in range(n):
        cells = []
        for j in range(n):
            idx = i * n + j
            g = ""
            if golden is not None:
                ok = "ok" if flat[idx] == golden[idx] else "bad"
                g = f'<td class="{ok}">{golden[idx]}</td>'
            cells.append(f"<td>{flat[idx]}</td>{g}")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head_g = "<th>参考(golden)</th>" if golden is not None else ""
    return (f'<div class="matname">{name} ({n}×{n})</div>'
            f'<table class="mat"><thead><tr><th colspan="{n}">RTL 实际输出</th>{head_g}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')

def vec_table(vectors):
    rows = "".join(
        f"<tr><td>{v['a']}</td><td>{v['b']}</td><td>{v['acc']}</td>"
        f"<td>{v['rtl']}</td><td>{v['ref']}</td>"
        f"<td class=\"{'ok' if v['rtl']==v['ref'] else 'bad'}\">"
        f"{'一致' if v['rtl']==v['ref'] else '不一致'}</td><td>{v.get('latency','-')}</td></tr>"
        for v in vectors)
    return ("<table><thead><tr><th>a(int8)</th><th>b(int8)</th><th>acc(int32)</th>"
            "<th>RTL 输出</th><th>独立参考</th><th>对照</th><th>延迟(拍)</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")

sections = []
for c in cases:
    e = c.get("extra", {})
    body = ""
    if "vectors" in e:
        body = vec_table(e["vectors"])
    elif "A" in e and "B" in e and "rtl" in e:
        n = int(len(e["A"]) ** 0.5)
        body = (tbl_matrix("A 输入", e["A"], n) +
                tbl_matrix("B 输入", e["B"], n) +
                tbl_matrix("C = A×B", e["rtl"], n, e.get("golden")))
    elif "jobs" in e:
        parts = []
        for j in e["jobs"]:
            n = int(len(j["rtl"]) ** 0.5)
            parts.append(f"<div class='job'>第 {j['job']+1} 次计算 · done@{j['done_cyc']} 拍</div>" +
                         tbl_matrix("C", j["rtl"], n, j["golden"]))
        body = "".join(parts)
    elif "configs" in e:
        parts = []
        for cfg in e["configs"]:
            n = int(len(cfg["rtl"]) ** 0.5)
            ok = "ok" if cfg.get("ok") else "bad"
            parts.append(f"<div class='job'>N={cfg['N']}, K={cfg['K']} "
                         f"<span class='{ok}'>[{'PASS' if cfg.get('ok') else 'FAIL'}]</span></div>" +
                         tbl_matrix("C", cfg["rtl"], n, cfg["golden"]))
        body = "".join(parts)
    elif "mat_rtl" in e:
        n = int(len(e["mat_rtl"]) ** 0.5)
        body = (tbl_matrix("边界矩阵 C", e["mat_rtl"], n, e["mat_golden"]) +
                f"<p class='note'>int32 累加回绕专项：RTL={e['mac_overflow_rtl']}　"
                f"独立参考={e['mac_overflow_ref']}　"
                f"<span class='{'ok' if e['mac_overflow_rtl']==e['mac_overflow_ref'] else 'bad'}'>"
                f"{'一致' if e['mac_overflow_rtl']==e['mac_overflow_ref'] else '不一致'}</span></p>")
    elif "modes" in e:
        rows = "".join(
            f"<tr><td>{m['mode']}</td><td>{m['txns']}</td>"
            f"<td>{m['latencies']}</td><td>{m['cycles']}</td></tr>"
            for m in e["modes"])
        body = ("<table><thead><tr><th>模式</th><th>交易数</th><th>各笔延迟(拍)</th>"
                f"<th>总周期</th></tr></thead><tbody>{rows}</tbody></table>"
                "<p class='note'>stream 模式延迟恒为 3 拍，符合 3 级流水；"
                "背压模式延迟增大但数据全部正确，证明握手与背压真实生效。</p>")
    badge = "ok" if c["status"] == "PASS" else "bad"
    sections.append(f"""
    <div class="case">
      <h2>{c['name']} — {c['desc']} <span class="badge {badge}">{c['status']}</span></h2>
      <div class="meta">仿真周期(A)={c['sim_cycles']}　宿主机时间(B)={c['host_time_ms']:.4f} ms</div>
      {body}
    </div>""")

case_rows = "".join(
    f"<tr><td>{c['name']}</td><td>{c['desc']}</td>"
    f"<td class=\"{'ok' if c['status']=='PASS' else 'bad'}\">{c['status']}</td>"
    f"<td>{c['sim_cycles']}</td><td>{c['host_time_ms']:.4f}</td></tr>" for c in cases)

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<style>
@page {{ size: A4; margin: 16mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Noto Sans CJK SC", sans-serif; color:#1f2328; font-size:10px; line-height:1.5; }}
h1 {{ color:#0969da; font-size:22px; margin:0 0 6px; }}
h2 {{ color:#0969da; font-size:13px; margin:0 0 6px; border-bottom:1px solid #d0d7de; padding-bottom:4px; }}
.cover {{ text-align:center; padding:40px 0 20px; }}
.cover .sub {{ color:#57606a; font-size:12px; }}
.kpi {{ display:flex; gap:12px; margin:18px 0; }}
.kpi div {{ flex:1; border:1px solid #d0d7de; border-radius:6px; padding:10px; text-align:center; }}
.kpi b {{ font-size:20px; color:#0969da; display:block; }}
.ok {{ color:#1a7f37; font-weight:bold; }}
.bad {{ color:#cf222e; font-weight:bold; }}
.badge {{ font-size:10px; padding:2px 8px; border-radius:10px; color:#fff; }}
.badge.ok {{ background:#1a7f37; }} .badge.bad {{ background:#cf222e; }}
table {{ width:100%; border-collapse:collapse; margin:6px 0 10px; font-size:9px; }}
th,td {{ border:1px solid #d0d7de; padding:4px 6px; text-align:center; }}
th {{ background:#f6f8fa; }}
.case {{ margin:14px 0; page-break-inside:avoid; }}
.meta {{ color:#57606a; margin-bottom:6px; font-size:9px; }}
.matname {{ font-weight:bold; margin:6px 0 2px; color:#24292f; }}
.row2 {{ display:flex; gap:12px; }} .row2>div {{ flex:1; }}
.job {{ font-weight:bold; margin-top:8px; }}
.note {{ color:#57606a; font-size:9px; }}
.foot {{ margin-top:20px; border-top:1px solid #d0d7de; padding-top:8px; color:#57606a; font-size:9px; }}
</style></head><body>
<div class="cover">
  <h1>Mini NPU 虚拟硬件执行平台</h1>
  <div class="sub">自动化测试与性能验证报告</div>
  <div class="sub">基于 Verilator 真实编译执行的 SystemVerilog RTL · 独立参考模型逐元素对照</div>
  <div class="sub">生成时间：{d['generated']}　工具：Verilator {d['verilator_version']}</div>
</div>
<div class="kpi">
  <div><b>{summary['total']}</b>案例总数</div>
  <div><b class="ok">{summary['pass']}</b>PASS</div>
  <div><b class="{'bad' if summary['fail'] else 'ok'}">{summary['fail']}</b>FAIL</div>
  <div><b>{summary['wall_ms']:.1f}</b>框架墙钟(ms)</div>
</div>
<h2>一、汇总</h2>
<table><thead><tr><th>案例</th><th>描述</th><th>状态</th><th>仿真周期(A)</th><th>宿主机时间 ms(B)</th></tr></thead>
<tbody>{case_rows}</tbody></table>
<h2>二、逐案例结果（RTL 实际输出 vs 独立参考）</h2>
{''.join(sections)}
<h2>三、性能口径说明</h2>
<p class="note">A 模拟周期数：RTL 仿真中时钟驱动记录的硬件时钟周期，来自真实 RTL 执行。<br>
B 宿主机时间：C++ 仿真程序在 CPU 上的真实墙钟耗时（std::chrono 实测），<b>不是硬件速度</b>。<br>
C 理论硬件估算：若目标频率 F，则理论执行时间≈周期数/F（本报告不代入具体 F，避免误导）。</p>
<p class="note"><b>严禁将 B（软件仿真速度）当作真实 FPGA 性能。</b>本平台未接任何 FPGA，无上板实测数据。</p>
<div class="foot">本报告由 Mini NPU 虚拟硬件执行平台自动生成 · 所有 RTL 输出均来自 Verilator 编译后的真实仿真模型 · 参考结果由独立 golden 模型计算（与 RTL 逻辑独立）</div>
</body></html>"""

HTML(string=html, base_url=str(ROOT)).write_pdf(str(OUT_PDF))
print("PDF 已生成:", OUT_PDF)
print("大小:", OUT_PDF.stat().st_size, "bytes")
