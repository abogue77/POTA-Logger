How do I start build_windows.bat?

You can run it a few ways:

From File Explorer:
Double-click build_windows.bat in the c:\Users\User\Git_Clone\POTA-Logger\ folder.

From a terminal (Command Prompt):

cd c:\Users\User\Git_Clone\POTA-Logger
build_windows.bat

From PowerShell:

cd c:\Users\User\Git_Clone\POTA-Logger
.\build_windows.bat


The script will check for PyInstaller (install it if missing), build the exe, and output it to dist\POTA-Logger.exe. A window will stay open at the end so you can see the result — press any key to close it.