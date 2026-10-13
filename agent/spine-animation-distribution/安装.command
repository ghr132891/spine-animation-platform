#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3，请先安装 Python 3.9 或以上。"
  read -r ignored
  exit 1
fi
python3 spine-animation/scripts/install.py
result=$?
echo "按回车关闭窗口。"
read -r ignored
exit "$result"
