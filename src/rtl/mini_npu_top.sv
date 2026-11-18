//==============================================================================
// File      : mini_npu_top.sv
// Desc      : 迷你 NPU 顶层整合示例（示意）
//             展示如何把 matmul_array 作为计算引擎挂载到简单控制接口上。
//             实际项目中可把 cfg_* 端口替换为 AXI-Lite/APB 寄存器堆，
//             并在 RECV 之后串接 bias / ReLU / 量化流水级。
//==============================================================================
module mini_npu_top #(
  parameter int N = 4
)(
  input  logic                    clk,
  input  logic                    rst_n,

  //---- 简化配置接口（start 脉冲 + 打包矩阵）----
  input  logic                    start,
  input  logic [N*N*8-1:0]       cfg_a,
  input  logic [N*N*8-1:0]       cfg_b,

  //---- 结果接口 ----
  output logic                    done,
  output logic [N*N*32-1:0]      result
);

  //---- 计算引擎接口 ----
  logic              in_valid, in_ready;
  logic              out_valid, out_ready;
  logic [N*N*8-1:0]  a_vec, b_vec;
  logic [N*N*32-1:0] c_vec;

  matmul_array #(
    .N  (N),
    .AW (8),
    .CW (32)
  ) u_compute (
    .clk       (clk),
    .rst_n     (rst_n),
    .in_valid  (in_valid),
    .in_ready  (in_ready),
    .a_vec     (a_vec),
    .b_vec     (b_vec),
    .out_valid (out_valid),
    .out_ready (out_ready),
    .c_vec     (c_vec)
  );

  //---- 顶层控制 FSM：配置 -> 发送 -> 接收 ----
  typedef enum logic [1:0] {
    T_IDLE = 2'd0,
    T_SEND = 2'd1,
    T_RECV = 2'd2
  } top_st_t;

  top_st_t st;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      st        <= T_IDLE;
      in_valid  <= 1'b0;
      out_ready <= 1'b0;
      a_vec     <= '0;
      b_vec     <= '0;
      result    <= '0;
      done      <= 1'b0;
    end else begin
      done <= 1'b0;                 // 单周期脉冲
      case (st)
        T_IDLE: begin
          if (start) begin
            a_vec    <= cfg_a;
            b_vec    <= cfg_b;
            in_valid <= 1'b1;
            st       <= T_SEND;
          end
        end

        T_SEND: begin
          // 矩阵引擎在 IDLE 才会拉高 in_ready；收到接受后转入接收
          if (in_valid && in_ready) begin
            in_valid  <= 1'b0;
            out_ready <= 1'b1;
            st        <= T_RECV;
          end
        end

        T_RECV: begin
          if (out_valid && out_ready) begin
            result    <= c_vec;
            out_ready <= 1'b0;
            done      <= 1'b1;
            st        <= T_IDLE;
          end
        end

        default: st <= T_IDLE;
      endcase
    end
  end

endmodule
