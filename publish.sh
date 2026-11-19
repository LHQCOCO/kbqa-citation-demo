#!/usr/bin/env bash
# 本地发布准备：跑测试 → 初始化 git → 首次提交 → 打印建仓与推送步骤。
# 本机没有 gh CLI，远端仓库请在浏览器里创建，脚本不会自动建仓或推送。
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

PY="${PYTHON:-python3.11}"

echo "==> 1/3 运行测试"
"$PY" -m pytest -q

echo "==> 2/3 初始化本地仓库"
if [ ! -d .git ]; then
  git init -b main
fi
git add -A
if git diff --cached --quiet; then
  echo "没有需要提交的改动"
else
  git commit -m "chore: 公开仓首版（离线 KBQA 抽取式问答演示）"
fi

echo "==> 3/3 后续手动步骤（浏览器建仓，然后推送）"
cat <<'TXT'
1. 浏览器打开 https://github.com/new
2. 仓库名建议：kbqa-citation-demo，选择 Public，不要勾选 README/.gitignore/LICENSE 模板
3. 建好后复制仓库页面给出的 SSH 地址，在本目录执行：
     git remote add origin <粘贴仓库地址>
     git push -u origin main
4. 推送后到 Actions 页面确认 Ubuntu / Python 3.11 与 3.12 两个 job 都变绿
TXT
