#!/usr/bin/env bash
# run.sh — 一键运行：构建 + 完整验证 + 综合估算
set -euo pipefail
cd "$(dirname "$0")"
echo "############ STEP 1: BUILD ############"
bash scripts/build.sh
echo "############ STEP 2: VERIFY ############"
python3 scripts/run_tests.py
echo "############ STEP 3: SYNTHESIS ESTIMATE ############"
bash scripts/synth_report.sh
echo "############ ALL DONE ############"
echo "查看报告: results/reports/latest_report.md"
