# Mini NPU 虚拟硬件执行与性能验证平台

> 在普通 CPU 计算机上编译、运行、验证真实 SystemVerilog NPU RTL —— **不依赖 FPGA 硬件或云服务器**。
> 核心：用 **Verilator** 把我们自己的 RTL 编译成 C++ 仿真模型，由 C++ harness 驱动真正执行；
> Python 用**独立参考模型**对照验证并统计性能。参考程序只做对照，绝不代替 RTL 执行。

---

## 一、它做什么 / 不做什么

**做**：
- 读取真实的 `mac_pipelined.sv` / `matmul_array.sv` / `mini_npu_top.sv`，用 Verilator 编译为 C++ 模型。
- 由 C++ 驱动注入时钟、复位、valid/ready 握手、矩阵数据，执行真实 RTL 计算。
- 独立 golden 模型逐元素比对，判定 PASS/FAIL，记录错误位置与输入。
- 统计模拟周期数、宿主机真实运行时间、理论硬件估算（三者严格区分）。
- Yosys 综合资源估算；命令行 + Web 两种使用方式。

**不做（明确边界）**：
- 不是 Python 数学计算器；计算结果来自编译后的 RTL。
- 不产生任何 FPGA 上板结果；软件仿真速度 ≠ 真实 FPGA 性能。
- 不修改原始 RTL（平台使用 `src/rtl/` 下的副本，原始工程在 `../rtl/`）。

---

## 二、目录结构

```
npu_platform/
├── README.md                     本文件
├── run.sh                        一键运行：构建→验证→综合估算
├── web_app.py                    Web 控制界面后端 (Flask)
├── templates/index.html          Web 前端
├── src/
│   ├── rtl/                      RTL 副本（源自 ../rtl/，角色原始版本）
│   │   ├── mac_pipelined.sv
│   │   ├── matmul_array.sv
│   │   └── mini_npu_top.sv
│   └── harness/                  C++ 仿真驱动
│       ├── npu_harness.h         公共库：时钟/周期/计时/JSON
│       ├── sim_mac.cpp           MAC 驱动（单笔/连流/背压/复位/随机）
│       ├── sim_matmul.cpp        矩阵驱动（参数化 N/K）
│       └── sim_npu.cpp           NPU 顶层驱动
├── scripts/
│   ├── build.sh                  一键构建（Verilator 编译所有配置）
│   ├── run_tests.py              自动化验证 + 性能统计 + 报告
│   ├── golden.py                 独立参考模型
│   └── synth_report.sh           Yosys 综合资源估算
├── tests/                        （预留扩展测试）
└── results/
    ├── logs/                     构建/综合/Web 日志
    └── reports/                  测试报告 (.json/.md)，含 latest_*
```

---

## 三、依赖安装

已在 Ubuntu 24.04 上验证。所需工具：

```bash
sudo apt-get update
sudo apt-get install -y verilator yosys g++ make python3
pip3 install flask          # Web 界面所需；命令行模式不需要
```

版本（本平台实测）：Verilator 5.020、Yosys 0.33、g++ 13.3、Python 3.11、Flask 3.1。

> 适配说明：RTL 中的 WIDTHEXPAND 是已确认的良性符号扩展警告；构建脚本用 `-Wno-fatal`
> 抑制其升级为错误，**不修改 RTL**。Verilator 5.020 的 `VerilatedContext` 用 `time()` API，
> 已固化在 `npu_harness.h` 中。

---

## 四、一键使用

```bash
cd npu_platform

# 方式 A：一键构建 + 全部验证 + 综合估算
./run.sh

# 方式 B：分步
bash scripts/build.sh                 # 仅构建
python3 scripts/run_tests.py          # 仅验证（7 案例）
python3 scripts/run_tests.py --case 案例2   # 单案例
bash scripts/synth_report.sh          # 综合资源估算

# 方式 C：Web 界面
python3 web_app.py                    # 然后浏览器访问 http://<host>:8080
```

---

## 五、测试结果（真实执行，非编造）

7 个强制案例全部 PASS（详见 `results/reports/latest_report.md`）：

| 案例 | 内容 | 状态 |
|------|------|------|
| 案例1 | INT8×INT8→INT32 累加（含 127×-128 边界） | PASS |
| 案例2 | 2×2 矩阵乘 A=[1,2;3,4] B=[5,6;7,8] → **C=[19,22,43,50]** | PASS |
| 案例3 | 4×4 矩阵乘（单位阵×递增值） | PASS |
| 案例4 | NPU 顶层连续 5 次矩阵计算 | PASS |
| 案例5 | 参数配置 N4K2 / N4K6 / N1K1 | PASS |
| 案例6 | 边界与 int32 累加回绕（INT_MAX 溢出） | PASS |
| 案例7 | 复位/握手/背压/流水延迟（延迟=3） | PASS |

Yosys 综合 cell 数（理论估算）：MAC 793、matmul 4×4 15089、NPU 顶层 15943。

---

## 六、性能统计口径（三类严格区分）

- **A 模拟周期数**：RTL 仿真中 clock driver 记录的硬件时钟周期，来自真实 RTL 执行（如 2×2 矩阵 13 周期）。
- **B 宿主机时间**：C++ 仿真程序在 CPU 上的真实墙钟耗时（std::chrono 实测，微秒级），**这是软件仿真速度，不是硬件速度**。
- **C 理论硬件估算**：执行时间 ≈ 周期数 / 目标频率；本平台不代入具体 FPGA 频率，避免误导。

> 严禁将 B 当作 FPGA 性能。达到"真实硬件性能"必须在 FPGA 上板实测（见第八节）。

---

## 七、已完成 / 未完成清单

**已完成**：
- [x] 检查 7 个源码文件（全部真实存在且完整）
- [x] Verilator 编译环境搭建与 API 适配（5.020）
- [x] MAC 模块真实仿真（单笔/连流/背压/复位/随机，延迟=3 验证）
- [x] 矩阵模块真实仿真（2×2 给定案例、4×4、边界、N/K 参数化、背压）
- [x] NPU 顶层完整层次连接与运行
- [x] 自动化验证系统（7 案例、独立 golden、逐元素比对、错误定位）
- [x] 性能统计系统（三类指标区分）
- [x] Yosys 综合资源估算
- [x] 一键构建/运行脚本
- [x] Web 控制界面（源码浏览/上传、测试选择、运行、结果、报告下载）
- [x] 测试报告导出（JSON + Markdown）

**未完成 / 不在本版范围**：
- [ ] 真实 FPGA 上板综合、布局布线、时序收敛与运行（本平台无 FPGA）
- [ ] 理论频率 F 的具体数值代入（需目标器件确定后）
- [ ] 卷积/im2col、bias/ReLU/量化后处理（架构变更，需另行立项）
- [ ] Web 界面的源码上传后自动重新构建（当前需手动点"重新构建 RTL"）
- [ ] 大规模矩阵的 BRAM/DMA 优化

---

## 八、后续接入真实 FPGA 的技术扩展方案

本平台的 RTL 与验证基础设施可直接复用到云端 FPGA（如华为云 FACS，详见 `../docs/FPGA_MIGRATION_PLAN.md`）：

1. **RTL 复用**：`src/rtl/` 三个内核零改动移植；本平台的 golden 模型与测试向量可迁移到云端作为回读比对基准。
2. **接口适配**：新增 AXI4-Lite wrapper 与对接 Shell 的顶层（本平台 `.sv` 如需上板按此扩展）。
3. **流程衔接**：本平台的"模拟周期数"可换算为理论硬件时间；上板后用 ILA 抓取握手波形，与本平台记录的周期/延迟对照，验证软硬一致。
4. **验证闭环**：上板后把实际回读结果接入本平台的 golden 对照框架，形成"仿真—综合—上板"一致的验证闭环。

---

## 九、验收标准对照

| 验收项 | 状态 |
|--------|------|
| 读取真实 SystemVerilog 源码 | ✅ 已实现 |
| Verilator 成功编译 RTL | ✅ 已实现 |
| 执行编译后的 RTL 仿真模型 | ✅ 已实现 |
| 完成实际矩阵乘法运算 | ✅ 2×2 得 19,22,43,50 |
| 输出正确计算结果 | ✅ 已实现 |
| 与独立参考结果自动对比 | ✅ golden.py 逐元素比对 |
| 统计模拟周期数与软件运行时间 | ✅ A/B 分列 |
| 生成可重复执行的测试报告 | ✅ run.sh + latest_report.md |
