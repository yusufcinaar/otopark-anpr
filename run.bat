@echo off
setlocal
chcp 65001 >nul
title Otopark ANPR Sistemi

cd /d "%~dp0"
set "VENV_PYTHON=%LocalAppData%\OtoparkANPRPublic\runtime\.venv-py312\Scripts\python.exe"
set "VENV_PYTHONW=%LocalAppData%\OtoparkANPRPublic\runtime\.venv-py312\Scripts\pythonw.exe"

:: Sanal ortam yoksa veya baska bilgisayardan kopyalanmis/bozuksa otomatik kur
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; print(sys.version)" >nul 2>nul
)
if not exist "%VENV_PYTHON%" goto :install
if errorlevel 1 goto :install
if not exist "%VENV_PYTHONW%" goto :install
goto :venv_ok

:install
    echo Sanal ortam bulunamadi veya bu bilgisayarda calismiyor.
    echo Otomatik kurulum baslatiliyor...
    echo.
    call kur.bat
    if errorlevel 1 exit /b 1
    goto :venv_ok

:venv_ok

if not exist "%VENV_PYTHONW%" (
    echo [HATA] Penceresiz Python bulunamadi. kur.bat ile kurulumu onarin.
    pause
    exit /b 1
)

:: .env yoksa olustur
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo .env dosyasi olusturuldu. Ayarlari doldurun.
    )
)

:: Veritabani kontrolunu app.main yapar; hata olursa GUI baslaticisi bildirir.

echo Otopark Plaka Tanima Sistemi baslatiliyor...
echo.
echo Webhook: http://0.0.0.0:8090/api/camera-event
echo Giris: admin / admin
echo.

:: pythonw Windows arayuzunu konsol baglamadan acarken start BAT'in bitmesini saglar.
start "" "%VENV_PYTHONW%" -m app.gui_launcher
if errorlevel 1 (
    echo.
    echo [HATA] Uygulama baslatilamadi!
    echo Eksik kutuphaneler olabilir. Kurulumu yeniden calistirin: kur.bat
    pause
    exit /b 1
)
exit /b 0
