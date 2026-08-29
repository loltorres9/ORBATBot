@echo off
REM Baut PDF-Merger.exe - eine einzelne Datei, die ohne Python laeuft.
REM Voraussetzung: Python 3.9+ ist installiert und in PATH.
setlocal
cd /d "%~dp0"

echo [1/3] Abhaengigkeiten installieren...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :fehler

echo [2/3] Exe bauen...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "PDF-Merger" ^
  --collect-submodules pypdf ^
  pdfmerge.py || goto :fehler

echo [3/3] Fertig: "%~dp0dist\PDF-Merger.exe"
start "" "%~dp0dist"
goto :ende

:fehler
echo.
echo Build fehlgeschlagen - siehe Meldungen oben.
exit /b 1

:ende
endlocal
