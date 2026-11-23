@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================================
echo      MOREIRA SPEC - Build do Executavel Portatil
echo ========================================================
echo.

:: Detecta o interpretador Python disponivel
set "PYTHON_EXE="
where python >nul 2>&1
if %errorlevel%==0 (
    python --version >nul 2>&1
    if !errorlevel!==0 set "PYTHON_EXE=python"
)

if "%PYTHON_EXE%"=="" (
    where py >nul 2>&1
    if !errorlevel!==0 (
        py -3 --version >nul 2>&1
        if !errorlevel!==0 set "PYTHON_EXE=py -3"
    )
)

if "%PYTHON_EXE%"=="" (
    if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [ERRO] Python 3.10+ nao encontrado no sistema.
    echo Instale o Python em https://www.python.org/downloads/ e marque "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/3] Usando Python: %PYTHON_EXE%
%PYTHON_EXE% --version
echo.

echo [2/3] Instalando/atualizando dependencias do requirements.txt...
%PYTHON_EXE% -m pip install --upgrade pip
%PYTHON_EXE% -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha ao instalar dependencias. Verifique sua conexao com a internet.
    pause
    exit /b 1
)

echo.
echo [3/3] Compilando MoreiraSpec.exe (executavel unico portatil)...
%PYTHON_EXE% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --noupx ^
    --onefile ^
    --noconsole ^
    --name "MoreiraSpec" ^
    --icon=icon.ico ^
    --version-file=version_info.txt ^
    --add-data "icon.ico;." ^
    --add-data "logo.png;." ^
    --collect-all customtkinter ^
    --hidden-import=wmi ^
    --hidden-import=win32com ^
    --hidden-import=win32com.client ^
    --hidden-import=pythoncom ^
    --hidden-import=pywintypes ^
    main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha durante a compilacao com PyInstaller.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   BUILD CONCLUIDO COM SUCESSO!
echo   Executavel gerado em: dist\MoreiraSpec.exe
echo ========================================================
echo.
pause
