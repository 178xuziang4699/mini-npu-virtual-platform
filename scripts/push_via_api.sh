#!/usr/bin/env bash
# 通过 GitHub Contents API 上传文件（绕过 git HTTPS 直连受限问题）
# 依赖：已 gh auth login，且 repo 已创建
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="178xuziang4699/mini-npu-virtual-platform"
BRANCH="main"
ok=0; fail=0

# 取 git 跟踪的文件列表
mapfile -t FILES < <(git -c core.quotepath=false ls-files)

echo "待上传文件数: ${#FILES[@]}"
for f in "${FILES[@]}"; do
  # 跳过刚才试水已传的 .gitignore（会显示为冲突，用 GET 判断）
  b64=$(base64 -w0 "$f")
  # 若文件已存在需带 sha，否则 422；先尝试不带 sha 创建
  if out=$(gh api --method PUT "repos/${REPO}/contents/${f}" \
        -f message="feat: add ${f}" \
        -f content="$b64" \
        -f branch="$BRANCH" 2>&1); then
    echo "  [OK]   $f"
    ok=$((ok+1))
  else
    # 可能是已存在(422)，尝试带 sha 更新
    sha=$(gh api "repos/${REPO}/contents/${f}?ref=${BRANCH}" --jq '.sha' 2>/dev/null || true)
    if [ -n "$sha" ]; then
      if gh api --method PUT "repos/${REPO}/contents/${f}" \
           -f message="chore: update ${f}" -f content="$b64" \
           -f sha="$sha" -f branch="$BRANCH" >/dev/null 2>&1; then
        echo "  [UPD]  $f"
        ok=$((ok+1))
      else
        echo "  [FAIL] $f (update failed)"
        fail=$((fail+1))
      fi
    else
      echo "  [FAIL] $f"
      echo "         $(echo "$out" | head -1)"
      fail=$((fail+1))
    fi
  fi
done

echo "================================"
echo "上传完成: OK=$ok  FAIL=$fail"
