//==============================================================================
// npu_harness.h — Mini NPU 虚拟硬件执行平台公共 C++ 驱动库
// 功能：时钟驱动、周期计数、宿主机计时、复位、JSON 结果输出
// 适配：Verilator 5.020 (VerilatedContext::time() API)
// 说明：所有激励由 C++ 驱动 Verilator 编译生成的 RTL C++ 模型，
//       RTL 真正参与仿真执行；本库只做驱动与统计，不做硬件计算。
//==============================================================================
#ifndef NPU_HARNESS_H
#define NPU_HARNESS_H

#include <cstdint>
#include <chrono>
#include <string>
#include <sstream>
#include <vector>

// ---- 有符号位宽适配：把 int 值写入 n 位有符号端口（补码）----
inline uint8_t  to_bits8 (int32_t v) { return static_cast<uint8_t>(static_cast<int8_t>(v)); }
inline uint32_t to_bits32(int32_t v) { return static_cast<uint32_t>(v); }
// 从无符号位容器还原有符号值
inline int8_t   from_bits8 (uint8_t  v) { return static_cast<int8_t>(v); }
inline int32_t  from_bits32(uint32_t v) { return static_cast<int32_t>(v); }

// ---- 高精度宿主机计时（用于统计软件运行真实时间，与仿真周期无关）----
class HostTimer {
  using clock = std::chrono::high_resolution_clock;
  clock::time_point t0_;
public:
  void start() { t0_ = clock::now(); }
  double elapsed_ms() const {
    return std::chrono::duration<double, std::milli>(clock::now() - t0_).count();
  }
};

// ---- 仿真上下文封装：时钟 + 周期计数 ----
// 每个时钟周期固定为：clk=0(eval) -> clk=1(eval)，上升沿采样。
template <typename TDUT>
class ClockDriver {
public:
  TDUT* dut;
  uint64_t cycles;   // 已经过的时钟周期数（模拟周期）

  explicit ClockDriver(TDUT* d) : dut(d), cycles(0) {}

  // 推进一个完整时钟周期（低->高->低 的边界由调用方决定激励时刻）
  void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
    cycles++;
  }
};

// ---- JSON 辅助：转义字符串 ----
inline std::string jstr(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') o += '\\';
    o += c;
  }
  o += "\"";
  return o;
}

#endif // NPU_HARNESS_H
