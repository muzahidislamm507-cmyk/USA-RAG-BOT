@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Bot chalu hocche... Browser-e jan: http://127.0.0.1:8000
echo Bondho korte chaile ei janalar upor Ctrl+C chapun.
echo.
python -m uvicorn api:app --reload
echo.
echo Bot bondho hoye geche. Upore lal lekha thakle seta copy kore amake pathan.
pause
