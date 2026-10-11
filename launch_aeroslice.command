#!/bin/zsh
set -eu
cd "${0:A:h}"
if [ ! -x .venv/bin/python ]; then
  task_python=""
  for candidate in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 python3; do
    if "$candidate" -c 'import sys, tkinter; assert sys.version_info >= (3,10)' 2>/dev/null; then
      task_python="$candidate"
      break
    fi
  done
  if [ -z "$task_python" ]; then
    print 'Install Python 3.10+ with Tkinter (python.org macOS installer), then retry.'
    read '?Press Enter to close.'
    exit 1
  fi
  "$task_python" -m venv .venv
  .venv/bin/python -m pip install -r AeroSlice/requirements.txt
fi
if ! .venv/bin/python -c 'import numpy, scipy, matplotlib, serial' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r AeroSlice/requirements.txt
fi
if ! .venv/bin/python -c 'import tkinter' >/dev/null 2>&1; then
  print 'This Python environment needs Tkinter. Recreate .venv using Python with Tk support.'
  exit 1
fi
exec .venv/bin/python -m AeroSlice.main
