//==============================================================================
// sim_mac.cpp — MAC 模块的 Verilator C++ 驱动（阶段3）
// 驱动模型：每周期推进（negedge 设激励 / posedge 采样握手），用 FIFO 计分板
//           匹配输入握手与输出，天然支持连续流、背压、复位。
// 输出 JSON 仅含 RTL 实际输出与周期统计；参考计算由 Python 独立层完成。
// 用法：sim_mac --mode single|stream|backpressure|reset|random [--count N]
//              [--seed N] [--a V --b V --acc V] [--json]
//==============================================================================
#include "Vmac.h"
#include "verilated.h"
#include "npu_harness.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>
#include <deque>
#include <array>
#include <string>

struct Req { int32_t a,b,acc; uint64_t send_cyc; };
struct Xaction {
  int32_t a,b,acc; uint64_t send_cyc, recv_cyc; int32_t rtl_out; int latency;
};

static int64_t arg_int(int argc, char** argv, const char* key, int64_t def) {
  for (int i=0;i<argc-1;i++) if (!strcmp(argv[i],key)) return strtoll(argv[i+1],nullptr,0);
  return def;
}
static bool has_arg(int argc, char** argv, const char* key) {
  for (int i=0;i<argc;i++) if (!strcmp(argv[i],key)) return true;
  return false;
}

int main(int argc, char** argv) {
  const char* mode = "single";
  for (int i=0;i<argc-1;i++) if (!strcmp(argv[i],"--mode")) mode = argv[i+1];
  int64_t count   = arg_int(argc,argv,"--count", 8);
  int64_t seed    = arg_int(argc,argv,"--seed", 12345);
  int64_t in_a    = arg_int(argc,argv,"--a", 3);
  int64_t in_b    = arg_int(argc,argv,"--b", 4);
  int64_t in_acc  = arg_int(argc,argv,"--acc", 0);
  bool bp    = !strcmp(mode,"backpressure");
  bool json  = has_arg(argc,argv,"--json");

  VerilatedContext* ctx = new VerilatedContext;
  Vmac* dut = new Vmac{ctx};
  ctx->time(0);

  uint64_t cyc = 0;
  HostTimer timer;
  std::mt19937 rng((uint32_t)seed);
  std::deque<Req> pending;
  std::vector<Xaction> xs;

  auto tick = [&](uint8_t rst, uint8_t inval, uint8_t outrdy,
                  uint8_t a, uint8_t b, uint32_t acc) -> bool {
    // negedge：稳定输入，读取握手信号（此时为上一拍锁存的稳定值）
    dut->clk=0; dut->rst_n=rst; dut->in_valid=inval; dut->out_ready=outrdy;
    dut->a=a; dut->b=b; dut->acc_in=acc; dut->eval();
    // 在 negedge 判定握手：本拍 posedge 将据此推进流水线
    bool in_hs  = rst && inval && dut->in_ready;
    bool out_hs = rst && dut->out_valid && outrdy;
    if (in_hs)  pending.push_back({from_bits8(a),from_bits8(b),from_bits32(acc),cyc+1});
    if (out_hs && !pending.empty()) {
      Req r = pending.front(); pending.pop_front();
      xs.push_back({r.a,r.b,r.acc,r.send_cyc,cyc+1,from_bits32(dut->acc_out),
                    (int)((cyc+1)-r.send_cyc)});
    }
    // posedge：DUT 采样输入 / 推进流水线
    dut->clk=1; dut->eval(); cyc++;
    return in_hs;
  };

  // ---- 复位：同步复位低有效，2 拍 ----
  tick(0,0,1,0,0,0); tick(0,0,1,0,0,0); tick(1,0,1,0,0,0);

  // ---- 生成要发送的交易 ----
  std::vector<std::array<int32_t,3>> reqs;
  if (!strcmp(mode,"single")) {
    reqs.push_back({(int32_t)in_a,(int32_t)in_b,(int32_t)in_acc});
  } else if (!strcmp(mode,"stream")) {
    for (int i=0;i<count;i++) reqs.push_back({i-8,(i*3)%17-8,i*7});
  } else if (!strcmp(mode,"backpressure")) {
    for (int i=0;i<count;i++) reqs.push_back({(i%21)-10,((i*5)%21)-10,i*11});
  } else if (!strcmp(mode,"reset")) {
    for (int i=0;i<count;i++) reqs.push_back({(i%21)-10,((i*5)%21)-10,i*3});
  } else { // random
    for (int i=0;i<count;i++) reqs.push_back({(int)(rng()%256)-128,(int)(rng()%256)-128,(int32_t)rng()});
  }

  timer.start();
  size_t sent=0;
  int  reset_at = (!strcmp(mode,"reset")) ? (int)reqs.size()/2 : -1;
  bool reset_done = false;
  // 主循环：持续发送 + 持续接收，直到所有交易完成
  uint64_t guard=0;
  while ((sent<reqs.size() || !pending.empty() || xs.size()<reqs.size()) && guard<200000) {
    guard++;

    if (reset_at>=0 && (int)sent>=reset_at && !reset_done && pending.empty()) {
      // 在"前序交易已全部接收完毕、后续尚未发送"的间隙插入复位
      // 这样复位清除所有有效状态，且不丢失/重复任何交易
      tick(0,0,1,0,0,0); tick(0,0,1,0,0,0);
      pending.clear();
      tick(1,0,1,0,0,0);
      reset_done = true;
      continue;
    }

    uint8_t inv=0, a=0,b=0; uint32_t acc=0;
    if (sent<reqs.size()) {
      inv=1; a=to_bits8(reqs[sent][0]); b=to_bits8(reqs[sent][1]); acc=to_bits32(reqs[sent][2]);
    }
    uint8_t ordy = bp ? (uint8_t)((cyc%3)!=0) : 1; // 背压：每 3 拍拉低 1 拍
    bool hs = tick(1, inv, ordy, a,b,acc);
    if (hs) sent++;  // 仅真正握手才算发送成功
  }
  double host_ms = timer.elapsed_ms();

  if (json) {
    printf("{\n  \"module\": \"mac_pipelined\",\n  \"mode\": \"%s\",\n", mode);
    printf("  \"total_sim_cycles\": %llu,\n  \"host_time_ms\": %.6f,\n",
           (unsigned long long)cyc, host_ms);
    printf("  \"transactions\": [\n");
    for (size_t i=0;i<xs.size();i++){auto&x=xs[i];
      printf("    {\"a\":%d,\"b\":%d,\"acc\":%d,\"send_cyc\":%llu,\"recv_cyc\":%llu,\"rtl_out\":%d,\"latency\":%d}%s\n",
        x.a,x.b,x.acc,(unsigned long long)x.send_cyc,(unsigned long long)x.recv_cyc,
        x.rtl_out,x.latency,(i+1<xs.size()?",":""));}
    printf("  ]\n}\n");
  } else {
    printf("MAC mode=%s cycles=%llu host=%.4fms txns=%zu\n",mode,
      (unsigned long long)cyc,host_ms,xs.size());
    for(auto&x:xs) printf("  a=%4d b=%4d acc=%12d -> rtl_out=%12d (lat=%d)\n",
      x.a,x.b,x.acc,x.rtl_out,x.latency);
  }
  delete dut; delete ctx;
  return 0;
}
