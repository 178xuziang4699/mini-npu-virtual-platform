#!/usr/bin/env bash
#==============================================================================
# synth_report.sh — Yosys RTL 综合分析与硬件资源估算（阶段7）
# 输出：每个模块的 cell 数量统计，作为"理论硬件资源估算"依据
# 注意：Yosys 通用综合不等同 FPGA 厂商工具的 DSP/BRAM 映射与实际时序。
#==============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results/logs
echo "===== Yosys 综合资源估算 (Yosys $(yosys -V 2>&1 | awk '{print $2}')) ====="

yosys -p "read_verilog -sv src/rtl/mac_pipelined.sv; synth -top mac_pipelined; stat" > results/logs/synth_mac.log 2>&1
yosys -p "read_verilog -sv src/rtl/mac_pipelined.sv src/rtl/matmul_array.sv; chparam -set N 4 -set K 4 matmul_array; synth -top matmul_array; stat" > results/logs/synth_mat4.log 2>&1
yosys -p "read_verilog -sv src/rtl/mac_pipelined.sv src/rtl/matmul_array.sv src/rtl/mini_npu_top.sv; chparam -set N 4 mini_npu_top; synth -top mini_npu_top; stat" > results/logs/synth_npu.log 2>&1

for f in synth_mac synth_mat4 synth_npu; do
  echo "--- $f ---"
  grep -E 'Number of cells' results/logs/$f.log | tail -1 || echo "  (无统计行)"
done
echo "===== 详细日志见 results/logs/synth_*.log ====="
echo "完成。"
