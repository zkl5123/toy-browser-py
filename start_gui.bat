@echo off
rem toy-browser-py GUI launcher (tkinter, zero external deps).
rem Usage:
rem   start_gui.bat                  - open about:blank
rem   start_gui.bat <file.html>      - open a local HTML file
rem   start_gui.bat <http URL>       - open an http(s) page
rem
rem Python must be the project-bundled runtime (full 3.11.9).  If
rem "python" on PATH is missing tkinter, this launcher hard-codes the
rem absolute path so it still works.
cd /d "%~dp0"

set "PY=C:\Users\Administrator\Desktop\llm\python-full\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" gui.py %*
if errorlevel 1 (
  echo [start_gui] gui.py exited with code %errorlevel%
  pause
)
