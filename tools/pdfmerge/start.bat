@echo off
REM Startet den PDF-Merger direkt aus dem Quellcode (ohne Exe-Build).
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
start "" pythonw pdfmerge.py %*
