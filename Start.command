#!/bin/zsh
set -e
cd "${0:A:h}"
open_workbench() {
  if [[ "${1:-}" != '--no-open' ]]; then open http://127.0.0.1:8780/; fi
}
if curl -fsS http://127.0.0.1:8780/api/health >/dev/null 2>&1; then
  open_workbench "${1:-}"
  exit 0
fi
if [[ -z "${SPINE_PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then
    SPINE_PYTHON="$PWD/.venv/bin/python"
  elif [[ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
    SPINE_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  else
    SPINE_PYTHON=python3
  fi
fi
"$SPINE_PYTHON" -c 'import PIL, numpy' || { print '需要 Python 3.10+、Pillow 和 numpy。'; exit 1; }
mkdir -p storage
# Detach from the launching terminal; closing it must not stop the workbench.
nohup "$SPINE_PYTHON" app/server.py --host 0.0.0.0 --port 8780 </dev/null >>storage/server.log 2>&1 &!
for attempt in {1..40}; do
  if curl -fsS http://127.0.0.1:8780/api/health >/dev/null 2>&1; then
    print 'Spine 工作台已在后台运行，可以关闭此终端。重启电脑后请再次双击 Start.command。'
    open_workbench "${1:-}"
    exit 0
  fi
  sleep 0.25
done
print '启动失败，请查看 storage/server.log。'
exit 1
