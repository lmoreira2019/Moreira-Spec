@echo off
setlocal

:: Executa o Moreira Spec diretamente pelo Python oficial (assinado digitalmente),
:: evitando bloqueios do Controle Inteligente de Aplicativos em ambiente local.
set "PYW_EXE="

if exist "%LOCALAPPDATA%\Python\bin\pythonw.exe" (
    set "PYW_EXE=%LOCALAPPDATA%\Python\bin\pythonw.exe"
)

if "%PYW_EXE%"=="" (
    where pythonw >nul 2>&1
    if %errorlevel%==0 set "PYW_EXE=pythonw"
)

if "%PYW_EXE%"=="" (
    where pyw >nul 2>&1
    if %errorlevel%==0 set "PYW_EXE=pyw -3"
)

if "%PYW_EXE%"=="" (
    echo [ERRO] pythonw.exe nao encontrado. Instale o Python 3.10+.
    pause
    exit /b 1
)

start "" %PYW_EXE% "%~dp0main.py"
