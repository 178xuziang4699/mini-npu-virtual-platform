//==============================================================================
// File      : mac_pipelined.sv
// Desc      : 3 级流水线定点 MAC 单元：acc_out = acc_in + (a * b)
//             a / b : 8-bit 有符号 (int8)，位宽由 AW 配置
//             acc   : 32-bit 有符号 (int32)，位宽由 CW 配置
// 接口      : 输入、输出均为 valid/ready 握手，支持背压（stall 整条流水线）
// 复位      : 同步复位，低有效 rst_n
// 可综合性  : 纯 RTL，无 initial 逻辑依赖（参数检查仅在仿真生效）
//==============================================================================
module mac_pipelined #(
  parameter int AW = 8,      // 乘数位宽（有符号）
  parameter int CW = 32      // 累加器位宽（有符号），要求 CW >= 2*AW
)(
  input  logic                    clk,
  input  logic                    rst_n,          // 同步复位, 低有效

  //---- 输入侧握手 ----
  input  logic                    in_valid,
  output logic                    in_ready,
  input  logic signed [AW-1:0]    a,
  input  logic signed [AW-1:0]    b,
  input  logic signed [CW-1:0]    acc_in,

  //---- 输出侧握手 ----
  output logic                    out_valid,
  input  logic                    out_ready,
  output logic signed [CW-1:0]    acc_out
);

  //----------------------------------------------------------------------------
  // 参数合法性检查（仅仿真，不进入综合）
  //----------------------------------------------------------------------------
`ifndef SYNTHESIS
  initial begin
    if (CW < 2*AW)
      $error("mac_pipelined: CW(%0d) must be >= 2*AW(%0d)", CW, 2*AW);
  end
`endif

  //----------------------------------------------------------------------------
  // 流水线划分（3 级）
  //   S1 : 采样 a / b / acc_in           -> 输入寄存器
  //   S2 : 完成有符号乘法 (a*b)          -> 乘积寄存器 + 随行 acc
  //   S3 : 完成累加 (prod + acc)         -> 输出寄存器
  // 吞吐 : 1 op/cycle（下游持续就绪时）；延迟 : 3 cycles
  //----------------------------------------------------------------------------
  logic        valid_s1, valid_s2, valid_s3;   // 各级有效标志（与数据对齐）
  logic signed [AW-1:0] a_s1, b_s1;
  logic signed [CW-1:0] acc_s1;
  logic signed [CW-1:0] prod_s2, acc_s2;
  logic signed [CW-1:0] acc_s3;

  // 组合逻辑：符号扩展到 CW 后相乘 / 相加，避免位宽截断歧义
  logic signed [CW-1:0] a_ext, b_ext;
  assign a_ext   = a_s1;                 // 8 位有符号 -> 32 位有符号（自动符号扩展）
  assign b_ext   = b_s1;
  wire   signed [CW-1:0] mul_res = a_ext * b_ext;
  wire   signed [CW-1:0] add_res = prod_s2 + acc_s2;

  //----------------------------------------------------------------------------
  // 背压控制：输出有效但下游未就绪时，冻结整条流水线
  //   stall = 1 时所有流水寄存器保持，in_ready 拉低通知上游停止发送
  //----------------------------------------------------------------------------
  wire stall = valid_s3 && !out_ready;
  assign in_ready = !stall;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      // 同步复位：清空所有有效位与数据寄存器
      valid_s1 <= 1'b0;
      valid_s2 <= 1'b0;
      valid_s3 <= 1'b0;
      a_s1     <= '0;
      b_s1     <= '0;
      acc_s1   <= '0;
      prod_s2  <= '0;
      acc_s2   <= '0;
      acc_s3   <= '0;
    end else if (!stall) begin
      //-- S1 -> S2 : 采样输入 --
      valid_s1 <= in_valid;
      a_s1     <= a;
      b_s1     <= b;
      acc_s1   <= acc_in;

      //-- S2 -> S3 : 锁存乘积与随行累加值 --
      valid_s2 <= valid_s1;
      prod_s2  <= mul_res;
      acc_s2   <= acc_s1;

      //-- S3 -> OUT : 锁存求和结果 --
      valid_s3 <= valid_s2;
      acc_s3   <= add_res;
    end
    // stall=1：不更新任何寄存器，实现反压保持
  end

  //----------------------------------------------------------------------------
  // 输出直连输出寄存器
  //----------------------------------------------------------------------------
  assign out_valid = valid_s3;
  assign acc_out   = acc_s3;

endmodule
