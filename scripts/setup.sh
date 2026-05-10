#!/usr/bin/env bash
# CareerCoach AI · 项目初始化脚本
# 用法: ./scripts/setup.sh

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 CareerCoach AI 项目初始化${NC}"
echo ""

# ============================================================
# 1. 检查必备工具
# ============================================================
echo -e "${GREEN}📋 检查必备工具...${NC}"

check_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo -e "${RED}❌ $1 未安装${NC} ($2)"
    return 1
  fi
  echo -e "  ✅ $1"
  return 0
}

MISSING=0

check_tool git      "请装 git" || MISSING=1
check_tool node     "https://nodejs.org · 推荐 22+"
check_tool pnpm     "npm install -g pnpm@9"
check_tool python3  "推荐 3.12+"
check_tool uv       "pip install uv 或 brew install uv"
check_tool docker   "https://docker.com（用于 postgres/redis 等）"

if [ $MISSING -eq 1 ]; then
  echo -e "${RED}请装好必备工具后再运行${NC}"
  exit 1
fi

echo ""

# ============================================================
# 2. 配置 git
# ============================================================
echo -e "${GREEN}⚙️  配置 git...${NC}"

git config commit.template .gitmessage
echo -e "  ✅ commit 模板已设置"

# ============================================================
# 3. 安装 pre-commit hooks
# ============================================================
if command -v pre-commit >/dev/null 2>&1; then
  echo -e "${GREEN}🔒 安装 pre-commit hooks...${NC}"
  pre-commit install
  pre-commit install --hook-type commit-msg
  echo -e "  ✅ pre-commit + commit-msg hooks 已装"
else
  echo -e "${YELLOW}⚠️  pre-commit 未安装，建议: uv tool install pre-commit${NC}"
  echo -e "${YELLOW}   装好后再跑一次本脚本${NC}"
fi

# ============================================================
# 4. 安装 pre-push hook（防直推 main）
# ============================================================
echo -e "${GREEN}🚫 安装 pre-push hook（拦截直推 main）...${NC}"

cat > .git/hooks/pre-push << 'HOOK_EOF'
#!/bin/sh
# 拦截直接推送到 main 分支
protected_branch='main'
current_branch=$(git rev-parse --abbrev-ref HEAD)

if [ "$current_branch" = "$protected_branch" ]; then
    echo "❌ 不允许直推 main 分支！请用 PR。"
    echo "   1. git checkout -b feat/xxx-yyy"
    echo "   2. 提交 + push 到该分支"
    echo "   3. 在 GitHub 上开 PR"
    exit 1
fi

exit 0
HOOK_EOF

chmod +x .git/hooks/pre-push
echo -e "  ✅ pre-push hook 已装"

# ============================================================
# 5. 创建 .env（如果不存在）
# ============================================================
if [ ! -f .env ]; then
  echo -e "${GREEN}📝 创建 .env（从 .env.example 复制）...${NC}"
  cp .env.example .env
  echo -e "  ✅ .env 已创建"
  echo -e "${YELLOW}  ⚠️  请编辑 .env 填写真实值（特别是 LLM API key）${NC}"
else
  echo -e "  ⏭️  .env 已存在，跳过"
fi

# ============================================================
# 6. 提示后续步骤
# ============================================================
echo ""
echo -e "${GREEN}✅ 项目初始化完成${NC}"
echo ""
echo "下一步："
echo ""
echo "  📦 安装前端依赖："
echo "     pnpm install"
echo ""
echo "  📦 安装后端依赖（apps/api 初始化后）："
echo "     cd apps/api && uv sync"
echo ""
echo "  🐳 启动开发服务（postgres/redis/qdrant/langfuse）："
echo "     docker compose up -d"
echo ""
echo "  🔥 创建你的第一个分支："
echo "     git checkout -b feat/<owner>-<topic>"
echo ""
echo "  📚 必读文档："
echo "     docs/README.md           ← 文档索引"
echo "     CLAUDE.md                ← AI 上下文"
echo "     docs/careercoach-engineering.md  ← Sprint 0 任务"
echo ""
