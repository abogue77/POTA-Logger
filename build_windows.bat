@echo off
echo Checking for PyInstaller...
pip show pyinstaller >nul 2>&1 || pip install pyinstaller
echo.
echo Building POTA-Logger.exe...
pyinstaller --onefile --windowed --name "POTA-Logger" hamlog.pyw
echo.
echo Build complete. Executable is in: dist\POTA-Logger.exe
pause
