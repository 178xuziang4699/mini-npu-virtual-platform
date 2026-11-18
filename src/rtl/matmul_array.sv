//==============================================================================
// File      : matmul_array.sv
// Desc      : 参数化定点矩阵乘加阵列  C[N×N] = A[N×K] × B[K×N]
//             A: N×K int8, B: K×N int8, C: N×N int32
//             采用「输出驻留 (Output-Stationary)」数据流：
//               - 每个 PE(i,j) 独占并长期持有 C[i][j] 累加器
//               - A 第 i 行沿阵列第 i 行横向广播
//               - B 第 k 行沿阵列第 j 列纵向广播
//               - k = 0..K-1 分时迭代，每个 PE 调用流水线 MAC 完成一次累加
//             选择原因：累加器只在本 PE 内局部反馈，无需在阵列间搬移 int32
//                       部分和；结构与 mac_pipelined 的 acc_in/acc_out 天然
//                       匹配，控制规则、时序收敛容易，便于参数化扩展。
// 接口      : 矩阵级 valid/ready 握手；内部 MAC 之间也使用 valid/ready
// 复位      : 同步复位，低有效
//------------------------------------------------------------------------------
// 【第二阶段修改说明】（相对 pre_KN 快照）
//   1. 新增独立参数 K（默认 K=N，保证原 4×4 方阵零改动兼容）
//   2. 端口位宽：a_vec N*K*AW、b_vec K*N*AW、c_vec N*N*CW
//   3. 存储维度：a_reg[N][K]、b_reg[K][N]，acc_reg 仍 [N][N]
//   4. k 迭代终值由 N-1 改为 K-1
//   5. P2-1 修复：k_reg 位宽用 KW=max(1,$clog2(K))，消除 K=1 零宽度
//   6. mac_pipelined.sv 未改动，握手/背压状态机结构未改动
//==============================================================================
module matmul_array #(
  parameter int N  = 4,      // 输出行数 / A 行数 / B 列数
  parameter int K  = N,      // 内积维度（默认=N → 兼容原 N×N 方阵）
  parameter int AW = 8,      // A/B 元素位宽 (int8)
  parameter int CW = 32      // C 元素位宽 (int32)
)(
  input  logic                    clk,
  input  logic                    rst_n,

  //---- 矩阵输入握手：A、B 同时有效，行优先打包 ----
  //  A[i][k] = a_vec[(i*K + k)*AW +: AW]   i∈[0,N-1], k∈[0,K-1]
  //  B[k][j] = b_vec[(k*N + j)*AW +: AW]   k∈[0,K-1], j∈[0,N-1]
  input  logic                    in_valid,
  output logic                    in_ready,
  input  logic [N*K*AW-1:0]      a_vec,
  input  logic [K*N*AW-1:0]      b_vec,

  //---- 矩阵输出握手：C 行优先打包 ----
  //  C[i][j] = c_vec[(i*N + j)*CW +: CW]
  output logic                    out_valid,
  input  logic                    out_ready,
  output logic [N*N*CW-1:0]      c_vec
);

  //----------------------------------------------------------------------------
  // P2-1 修复：$clog2(1)=0 会导致零宽度；保证 k 计数器至少 1 位
  //----------------------------------------------------------------------------
  localparam int KW = ($clog2(K) > 0) ? $clog2(K) : 1;

  //----------------------------------------------------------------------------
  // 控制器状态机（结构与原版一致，仅 k 终值改为 K-1）
  //   S_IDLE  : 等待 A/B 输入
  //   S_ISSUE : 向所有 MAC 发起第 k 轮乘加（a=A[i][k], b=B[k][j]）
  //   S_WAIT  : 等待所有 MAC 结果返回并写回本地累加器
  //   S_OUT   : 输出 C，支持下游背压（out_ready=0 时保持结果）
  //----------------------------------------------------------------------------
  typedef enum logic [1:0] {
    S_IDLE  = 2'd0,
    S_ISSUE = 2'd1,
    S_WAIT  = 2'd2,
    S_OUT   = 2'd3
  } st_t;

  st_t state;
  logic [KW-1:0] k_reg;                  // 当前 k 迭代计数器（0..K-1）

  //----------------------------------------------------------------------------
  // 输入解包：打包向量 -> 二维有符号数组（组合）
  //----------------------------------------------------------------------------
  logic signed [AW-1:0] a_in2d [0:N-1][0:K-1];
  logic signed [AW-1:0] b_in2d [0:K-1][0:N-1];

  for (genvar i = 0; i < N; i++) begin : g_unpack_a_i
    for (genvar k = 0; k < K; k++) begin : g_unpack_a_k
      assign a_in2d[i][k] = a_vec[(i*K + k)*AW +: AW];
    end
  end
  for (genvar k = 0; k < K; k++) begin : g_unpack_b_k
    for (genvar j = 0; j < N; j++) begin : g_unpack_b_j
      assign b_in2d[k][j] = b_vec[(k*N + j)*AW +: AW];
    end
  end

  //----------------------------------------------------------------------------
  // 片上存储：A、B 输入寄存器 与 C 累加寄存器
  //----------------------------------------------------------------------------
  logic signed [AW-1:0] a_reg   [0:N-1][0:K-1];
  logic signed [AW-1:0] b_reg   [0:K-1][0:N-1];
  logic signed [CW-1:0] acc_reg [0:N-1][0:N-1];

  //----------------------------------------------------------------------------
  // MAC 阵列接口信号（一维展开，索引 = i*N + j）
  //----------------------------------------------------------------------------
  logic [N*N-1:0]            mac_in_valid, mac_in_ready;
  logic [N*N-1:0]            mac_out_valid, mac_out_ready;
  logic signed [AW-1:0]      mac_a     [0:N-1][0:N-1];
  logic signed [AW-1:0]      mac_b     [0:N-1][0:N-1];
  logic signed [CW-1:0]      mac_acc_in[0:N-1][0:N-1];
  logic signed [CW-1:0]      mac_acc_out[0:N-1][0:N-1];

  // 数据选择：输出驻留数据流
  //   PE(i,j) 在 k 轮取 A[i][k] 与 B[k][j]
  always_comb begin
    for (int i = 0; i < N; i++) begin
      for (int j = 0; j < N; j++) begin
        mac_a[i][j]      = a_reg[i][k_reg];
        mac_b[i][j]      = b_reg[k_reg][j];
        mac_acc_in[i][j] = acc_reg[i][j];
      end
    end
  end

  // 本设计始终消费 MAC 输出，因此 MAC 侧永不背压；矩阵级背压在 S_OUT 处理
  assign mac_out_ready = '1;
  assign mac_in_valid  = (state == S_ISSUE) ? {N*N{1'b1}} : {N*N{1'b0}};

  wire all_mac_in_ready  = &mac_in_ready;
  wire all_mac_out_valid = &mac_out_valid;

  //----------------------------------------------------------------------------
  // 实例化 N x N 个流水线 MAC（输出驻留 PE），K 维度由调度控制
  //----------------------------------------------------------------------------
  for (genvar gi = 0; gi < N; gi++) begin : g_row
    for (genvar gj = 0; gj < N; gj++) begin : g_col
      mac_pipelined #(
        .AW(AW),
        .CW(CW)
      ) u_mac (
        .clk        (clk),
        .rst_n      (rst_n),
        .in_valid   (mac_in_valid [gi*N + gj]),
        .in_ready   (mac_in_ready [gi*N + gj]),
        .a          (mac_a       [gi][gj]),
        .b          (mac_b       [gi][gj]),
        .acc_in     (mac_acc_in  [gi][gj]),
        .out_valid  (mac_out_valid[gi*N + gj]),
        .out_ready  (mac_out_ready[gi*N + gj]),
        .acc_out    (mac_acc_out [gi][gj])
      );
    end
  end

  //----------------------------------------------------------------------------
  // 数据寄存器更新
  //----------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < N; i++) begin
        for (int k = 0; k < K; k++) a_reg[i][k] <= '0;
        for (int j = 0; j < N; j++) acc_reg[i][j] <= '0;
      end
      for (int k = 0; k < K; k++) begin
        for (int j = 0; j < N; j++) b_reg[k][j] <= '0;
      end
    end else begin
      // 载入 A / B，并清零累加器
      if (state == S_IDLE && in_valid && in_ready) begin
        for (int i = 0; i < N; i++) begin
          for (int k = 0; k < K; k++) a_reg[i][k] <= a_in2d[i][k];
          for (int j = 0; j < N; j++) acc_reg[i][j] <= '0;
        end
        for (int k = 0; k < K; k++) begin
          for (int j = 0; j < N; j++) b_reg[k][j] <= b_in2d[k][j];
        end
      end
      // 写回 MAC 计算结果（第 k 轮完成）
      if (state == S_WAIT && all_mac_out_valid) begin
        for (int i = 0; i < N; i++) begin
          for (int j = 0; j < N; j++) begin
            acc_reg[i][j] <= mac_acc_out[i][j];
          end
        end
      end
    end
  end

  //----------------------------------------------------------------------------
  // 主状态机：输入 -> K 轮 MAC 迭代 -> 输出
  //----------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state <= S_IDLE;
      k_reg <= '0;
    end else begin
      case (state)
        S_IDLE: begin
          if (in_valid && in_ready) begin
            k_reg <= '0;
            state <= S_ISSUE;
          end
        end

        S_ISSUE: begin
          // MAC 侧永不背压，一拍后进入等待（in_ready 恒为 1）
          if (all_mac_in_ready)
            state <= S_WAIT;
        end

        S_WAIT: begin
          if (all_mac_out_valid) begin
            if (k_reg == K-1) begin
              state <= S_OUT;             // 所有 k 完成
            end else begin
              k_reg <= k_reg + 1'b1;      // 继续下一轮 k
              state <= S_ISSUE;
            end
          end
        end

        S_OUT: begin
          // 下游背压：out_ready=0 时保持状态与结果
          if (out_ready)
            state <= S_IDLE;
        end

        default: state <= S_IDLE;
      endcase
    end
  end

  //----------------------------------------------------------------------------
  // 矩阵级握手与输出打包
  //----------------------------------------------------------------------------
  assign in_ready  = (state == S_IDLE);
  assign out_valid = (state == S_OUT);

  for (genvar gi = 0; gi < N; gi++) begin : g_pack_i
    for (genvar gj = 0; gj < N; gj++) begin : g_pack_j
      assign c_vec[(gi*N + gj)*CW +: CW] = acc_reg[gi][gj];
    end
  end

endmodule
