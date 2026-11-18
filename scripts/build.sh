#!/usr/bin/env bash
#==============================================================================
# build.sh — Mini NPU 虚拟硬件执行平台 一键构建脚本
# 功能：用 Verilator 把 RTL 编译成 C++ 仿真模型 + harness 驱动，生成可执行文件
# 说明：-Wno-fatal 用于抑制 RTL 中已确认良性的 WIDTHEXPAND 警告（不修改 RTL）
#       -G<N/K> 覆盖 RTL 参数；-CFLAGS -D<NVAL/KVAL> 同步传给 C++ 打包逻辑
#==============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results/logs results/reports

RTL="src/rtl/mac_pipelined.sv src/rtl/matmul_array.sv src/rtl/mini_npu_top.sv"
HARNESS="src/harness"
INC="-Isrc/harness"
COMMON="-cc --exe --build -Wno-fatal"

echo "=============================================="
echo " Mini NPU 平台构建  (Verilator $(verilator --version 2>/dev/null | awk '{print $2}'))"
echo "=============================================="

build_mac() {
  echo "[1/6] MAC harness (mac_pipelined)"
  verilator $COMMON --top-module mac_pipelined --prefix Vmac \
    -Mdir obj_dir_mac $INC src/rtl/mac_pipelined.sv \
    $HARNESS/sim_mac.cpp -o sim_mac > results/logs/build_mac.log 2>&1
  echo "      -> obj_dir_mac/sim_mac"
}

build_mat() {  # $1=N $2=K $3=tag
  local N=$1 K=$2 TAG=$3
  echo "[mat] matmul N=$N K=$K -> obj_dir_$TAG"
  verilator $COMMON --top-module matmul_array --prefix Vmat \
    -Mdir obj_dir_$TAG $INC -GN=$N -GK=$K \
    -CFLAGS "-DNVAL=$N -DKVAL=$K" \
    src/rtl/mac_pipelined.sv src/rtl/matmul_array.sv \
    $HARNESS/sim_matmul.cpp -o sim_matmul > results/logs/build_mat_$TAG.log 2>&1
  echo "      -> obj_dir_$TAG/sim_matmul"
}

build_npu() {  # $1=N $2=tag
  local N=$1 TAG=$2
  echo "[npu] mini_npu_top N=$N -> obj_dir_$TAG"
  verilator $COMMON --top-module mini_npu_top --prefix Vnpu \
    -Mdir obj_dir_$TAG $INC -GN=$N -CFLAGS "-DNVAL=$N" \
    src/rtl/mac_pipelined.sv src/rtl/matmul_array.sv src/rtl/mini_npu_top.sv \
    $HARNESS/sim_npu.cpp -o sim_npu > results/logs/build_$TAG.log 2>&1
  echo "      -> obj_dir_$TAG/sim_npu"
}

build_mac
build_mat 2 2 mat2x2
build_mat 4 4 mat4x4
build_mat 4 2 mat4x2
build_mat 4 6 mat4x6
build_mat 1 1 mat1x1
build_npu 4 npu4

echo "=============================================="
echo "构建完成：全部仿真可执行文件已生成。"
echo "=============================================="
