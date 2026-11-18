//==============================================================================
// sim_matmul.cpp — 矩阵乘加阵列 matmul_array 的 Verilator C++ 驱动（阶段4）
// 功能：真正编译并驱动 matmul_array RTL 执行 C[N×N] = A[N×K] × B[K×N]
//       - 打包 A/B 到宽总线，按 valid/ready 握手发送
//       - 等待 out_valid，读取 c_vec，输出 RTL 实际结果
//       - 支持单任务、连续多任务、输出背压、复位
// 参数：N/K 由编译期宏 NVAL/KVAL 决定（须与 Verilator -GN/-GK 一致）
// 用法：sim_matmul --mode single|stream|backpressure [--count N] [--json] [--dump-a-b]
// 输入矩阵通过 --matrix "a11,a12,...,b11,..." 或 --a/--b 提供；参考由 Python 完成。
//==============================================================================
#include "Vmat.h"
#include "verilated.h"
#include "npu_harness.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
#include <sstream>
#include <random>

#ifndef NVAL
#define NVAL 4
#endif
#ifndef KVAL
#define KVAL 4
#endif
static const int N = NVAL, K = KVAL;
static const int AW=8, CW=32, WW=32; // word = 32bit

static int64_t arg_int(int argc,char**argv,const char*k,int64_t d){
  for(int i=0;i<argc-1;i++) if(!strcmp(argv[i],k)) return strtoll(argv[i+1],nullptr,0);
  return d;
}
static const char* arg_str(int argc,char**argv,const char*k,const char*d){
  for(int i=0;i<argc-1;i++) if(!strcmp(argv[i],k)) return argv[i+1];
  return d;
}
static bool has(int argc,char**argv,const char*k){
  for(int i=0;i<argc;i++) if(!strcmp(argv[i],k)) return true; return false;
}

// 把 int8 数组 A(N*K)、B(K*N) 打包进宽总线 words
static void pack_ab(const std::vector<int32_t>&A,const std::vector<int32_t>&B,
                    std::vector<uint32_t>&awords,std::vector<uint32_t>&bwords){
  int na=N*K, nb=K*N;
  int wa=(na*AW + WW-1)/WW, wb=(nb*AW + WW-1)/WW;
  awords.assign(wa,0); bwords.assign(wb,0);
  for(int idx=0;idx<na;idx++){
    uint32_t byte=to_bits8(A[idx]);
    awords[idx/(WW/AW)] |= byte << (8*(idx%(WW/AW)));
  }
  for(int idx=0;idx<nb;idx++){
    uint32_t byte=to_bits8(B[idx]);
    bwords[idx/(WW/AW)] |= byte << (8*(idx%(WW/AW)));
  }
}

struct Task { std::vector<int32_t> A,B; uint64_t send_cyc,recv_cyc; std::vector<int32_t> C; };

int main(int argc,char**argv){
  const char* mode=arg_str(argc,argv,"--mode","single");
  int64_t count=arg_int(argc,argv,"--count",4);
  int64_t seed=arg_int(argc,argv,"--seed",777);
  bool json=has(argc,argv,"--json");
  bool dump=has(argc,argv,"--dump-a-b");
  std::string mtxt=arg_str(argc,argv,"--matrix","");

  VerilatedContext* ctx=new VerilatedContext;
  Vmat* dut=new Vmat{ctx};
  ctx->time(0);
  uint64_t cyc=0;
  HostTimer timer;
  std::mt19937 rng((uint32_t)seed);

  // 解析或生成矩阵任务
  std::vector<Task> tasks;
  int total=!strcmp(mode,"single")?1:(int)count;
  for(int t=0;t<total;t++){
    Task tk; tk.A.resize(N*K); tk.B.resize(K*N); tk.C.resize(N*N);
    if(!mtxt.empty() && t==0){
      std::vector<int32_t> v; std::stringstream ss(mtxt); std::string it;
      while(std::getline(ss,it,',')) v.push_back((int32_t)strtol(it.c_str(),nullptr,0));
      // 期望顺序：A 的 N*K 个 + B 的 K*N 个
      for(int i=0;i<N*K;i++) tk.A[i]=(i<(int)v.size()?v[i]:0);
      for(int i=0;i<K*N;i++) tk.B[i]=((N*K+i)<(int)v.size()?v[N*K+i]:0);
    } else {
      for(int i=0;i<N*K;i++) tk.A[i]=(int)(rng()%21)-10;
      for(int i=0;i<K*N;i++) tk.B[i]=(int)(rng()%21)-10;
    }
    tasks.push_back(tk);
  }

  // 端口可能是标量(<=32bit)或数组(>32bit)，统一按 uint32_t 首地址访问
  uint32_t* a_vec_w = reinterpret_cast<uint32_t*>(&dut->a_vec);
  uint32_t* b_vec_w = reinterpret_cast<uint32_t*>(&dut->b_vec);
  uint32_t* c_vec_w = reinterpret_cast<uint32_t*>(&dut->c_vec);

  auto tick=[&](uint8_t rst,uint8_t inv,uint8_t ordy,
                uint32_t*aw,uint32_t* bw)->bool{
    dut->clk=0; dut->rst_n=rst; dut->in_valid=inv; dut->out_ready=ordy;
    int wa=(N*K*AW+WW-1)/WW, wb=(K*N*AW+WW-1)/WW;
    for(int w=0;w<wa;w++) a_vec_w[w]=(aw?aw[w]:0);
    for(int w=0;w<wb;w++) b_vec_w[w]=(bw?bw[w]:0);
    dut->eval();
    bool in_hs = rst && inv && dut->in_ready;
    dut->clk=1; dut->eval(); cyc++;
    return in_hs;
  };
  // 读 c_vec -> C[N][N]
  auto read_c=[&](std::vector<int32_t>&C){
    for(int i=0;i<N*N;i++) C[i]=from_bits32(c_vec_w[i]);
  };

  // 复位
  tick(0,0,1,nullptr,nullptr); tick(0,0,1,nullptr,nullptr); tick(1,0,1,nullptr,nullptr);

  bool bp=!strcmp(mode,"backpressure");
  timer.start();
  size_t sent=0;
  uint64_t guard=0;
  while((sent<tasks.size()) && guard<500000){
    guard++;
    Task& tk=tasks[sent];
    std::vector<uint32_t> aw,bw;
    pack_ab(tk.A,tk.B,aw,bw);

    // 发送阶段：等 in_ready 握手（negedge 判定）
    uint64_t send_cyc=0;
    for(int g=0;g<1000;g++){
      dut->clk=0; dut->rst_n=1; dut->in_valid=1; dut->out_ready=1;
      for(int w=0;w<(int)aw.size();w++) a_vec_w[w]=aw[w];
      for(int w=0;w<(int)bw.size();w++) b_vec_w[w]=bw[w];
      dut->eval();
      bool hs=dut->in_ready;
      dut->clk=1; dut->eval(); cyc++;
      if(hs){send_cyc=cyc;break;}
    }
    dut->in_valid=0;

    // 接收阶段：等 out_valid（背压模式周期性拉低 out_ready）
    uint64_t recv_cyc=0;
    for(int g=0;g<2000;g++){
      uint8_t ordy=bp?(uint8_t)((cyc%3)!=0):1;
      dut->clk=0; dut->rst_n=1; dut->in_valid=0; dut->out_ready=ordy; dut->eval();
      bool out_hs=dut->out_valid && ordy;
      if(out_hs){ read_c(tk.C); recv_cyc=cyc+1; }
      dut->clk=1; dut->eval(); cyc++;
      if(out_hs) break;
    }
    tk.send_cyc=send_cyc; tk.recv_cyc=recv_cyc;
    sent++;
  }
  double host_ms=timer.elapsed_ms();

  if(json){
    printf("{\n  \"module\":\"matmul_array\",\n  \"N\":%d,\"K\":%d,\n",N,K);
    printf("  \"mode\":\"%s\",\"total_sim_cycles\":%llu,\"host_time_ms\":%.6f,\n",
      mode,(unsigned long long)cyc,host_ms);
    printf("  \"tasks\":[\n");
    for(size_t t=0;t<tasks.size();t++){auto&tk=tasks[t];
      printf("    {\"send_cyc\":%llu,\"recv_cyc\":%llu,\"A\":[",
        (unsigned long long)tk.send_cyc,(unsigned long long)tk.recv_cyc);
      for(int i=0;i<N*K;i++) printf("%d%s",tk.A[i],(i+1<N*K?",":""));
      printf("],\"B\":[");
      for(int i=0;i<K*N;i++) printf("%d%s",tk.B[i],(i+1<K*N?",":""));
      printf("],\"C_rtl\":[");
      for(int i=0;i<N*N;i++) printf("%d%s",tk.C[i],(i+1<N*N?",":""));
      printf("]}%s\n",(t+1<tasks.size()?",":""));}
    printf("  ]\n}\n");
  } else {
    printf("matmul N=%d K=%d mode=%s cycles=%llu host=%.4fms tasks=%zu\n",
      N,K,mode,(unsigned long long)cyc,host_ms,tasks.size());
    for(auto&tk:tasks){
      printf("  C_rtl = ["); for(int i=0;i<N*N;i++) printf("%d ",tk.C[i]); printf("]\n");
    }
  }
  (void)dump;
  delete dut; delete ctx;
  return 0;
}
