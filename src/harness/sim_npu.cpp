//==============================================================================
// sim_npu.cpp — mini_npu_top 顶层的 Verilator C++ 驱动（阶段5）
// 功能：通过 start/cfg_a/cfg_b/done/result 端口驱动 NPU 顶层完成矩阵乘法
//       真正执行 Verilator 编译的完整 RTL 层次（npu->matmul->mac）
// 用法：sim_npu --matrix "A(N*N个),B(N*N个)" [--count N] [--json] [--reset]
//==============================================================================
#include "Vnpu.h"
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
static const int N=NVAL, AW=8, CW=32, WW=32;

static const char* arg_str(int c,char**v,const char*k,const char* d){
  for(int i=0;i<c-1;i++) if(!strcmp(v[i],k)) return v[i+1]; return d;}
static int64_t arg_int(int c,char**v,const char*k,int64_t d){
  for(int i=0;i<c-1;i++) if(!strcmp(v[i],k)) return strtoll(v[i+1],nullptr,0); return d;}
static bool has(int c,char**v,const char*k){
  for(int i=0;i<c;i++) if(!strcmp(v[i],k)) return true; return false;}

struct Job{ std::vector<int32_t> A,B,C; uint64_t done_cyc; };

int main(int argc,char**argv){
  bool json=has(argc,argv,"--json");
  bool do_reset=has(argc,argv,"--reset");
  int64_t count=arg_int(argc,argv,"--count",1);
  int64_t seed=arg_int(argc,argv,"--seed",2024);
  std::string mtxt=arg_str(argc,argv,"--matrix","");
  std::mt19937 rng((uint32_t)seed);

  VerilatedContext* ctx=new VerilatedContext;
  Vnpu* dut=new Vnpu{ctx};
  ctx->time(0);
  uint64_t cyc=0;
  uint32_t* cfg_a=reinterpret_cast<uint32_t*>(&dut->cfg_a);
  uint32_t* cfg_b=reinterpret_cast<uint32_t*>(&dut->cfg_b);
  uint32_t* result=reinterpret_cast<uint32_t*>(&dut->result);

  auto tick=[&](uint8_t rst,uint8_t start){
    dut->clk=0; dut->rst_n=rst; dut->start=start; dut->eval();
    dut->clk=1; dut->eval(); cyc++;
  };

  int words_mat=(N*N*AW+WW-1)/WW;
  // 复位
  tick(0,0); tick(0,0); tick(1,0);
  if(do_reset){ tick(0,0); tick(0,0); tick(1,0); }

  std::vector<Job> jobs;
  HostTimer timer; timer.start();
  for(int t=0;t<count;t++){
    Job j; j.A.resize(N*N); j.B.resize(N*N); j.C.resize(N*N);
    if(!mtxt.empty() && t==0){
      std::vector<int32_t> val; std::stringstream ss(mtxt); std::string it;
      while(std::getline(ss,it,',')) val.push_back((int32_t)strtol(it.c_str(),nullptr,0));
      for(int i=0;i<N*N;i++) j.A[i]=(i<(int)val.size()?val[i]:0);
      for(int i=0;i<N*N;i++) j.B[i]=((N*N+i)<(int)val.size()?val[N*N+i]:0);
    } else {
      for(int i=0;i<N*N;i++) j.A[i]=(int)(rng()%21)-10;
      for(int i=0;i<N*N;i++) j.B[i]=(int)(rng()%21)-10;
    }
    // 打包 A/B
    std::vector<uint32_t> aw(words_mat,0), bw(words_mat,0);
    for(int idx=0;idx<N*N;idx++){
      aw[idx/(WW/AW)] |= to_bits8(j.A[idx]) << (8*(idx%(WW/AW)));
      bw[idx/(WW/AW)] |= to_bits8(j.B[idx]) << (8*(idx%(WW/AW)));
    }
    for(int w=0;w<words_mat;w++){ cfg_a[w]=aw[w]; cfg_b[w]=bw[w]; }

    // 打 start 单脉冲（IDLE 态采样）
    tick(1,1);
    tick(1,0);
    // 等 done 脉冲
    uint64_t done_cyc=0;
    for(int g=0;g<500;g++){
      dut->clk=0; dut->rst_n=1; dut->start=0; dut->eval();
      bool done_hs=dut->done;
      if(done_hs){ done_cyc=cyc+1; for(int i=0;i<N*N;i++) j.C[i]=from_bits32(result[i]); }
      dut->clk=1; dut->eval(); cyc++;
      if(done_hs) break;
    }
    j.done_cyc=done_cyc;
    jobs.push_back(j);
  }
  double host_ms=timer.elapsed_ms();

  if(json){
    printf("{\n  \"module\":\"mini_npu_top\",\"N\":%d,\n",N);
    printf("  \"total_sim_cycles\":%llu,\"host_time_ms\":%.6f,\n",
      (unsigned long long)cyc,host_ms);
    printf("  \"jobs\":[\n");
    for(size_t t=0;t<jobs.size();t++){auto&j=jobs[t];
      printf("    {\"done_cyc\":%llu,\"A\":[",(unsigned long long)j.done_cyc);
      for(int i=0;i<N*N;i++) printf("%d%s",j.A[i],(i+1<N*N?",":""));
      printf("],\"B\":[");
      for(int i=0;i<N*N;i++) printf("%d%s",j.B[i],(i+1<N*N?",":""));
      printf("],\"C_rtl\":[");
      for(int i=0;i<N*N;i++) printf("%d%s",j.C[i],(i+1<N*N?",":""));
      printf("]}%s\n",(t+1<jobs.size()?",":""));}
    printf("  ]\n}\n");
  } else {
    printf("mini_npu_top N=%d cycles=%llu host=%.4fms jobs=%zu\n",
      N,(unsigned long long)cyc,host_ms,jobs.size());
    for(auto&j:jobs){ printf("  C_rtl=["); for(int i=0;i<N*N;i++) printf("%d ",j.C[i]); printf("]\n"); }
  }
  delete dut; delete ctx;
  return 0;
}
