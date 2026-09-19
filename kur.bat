@echo off
setlocal
chcp 65001 >nul
title Otopark ANPR - Kurulum
cd /d "%~dp0"
set "RUNTIME_DIR=%LocalAppData%\OtoparkANPRPublic\runtime"
set "VENV_DIR=%LocalAppData%\OtoparkANPRPublic\runtime\.venv-py312"
if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

echo.
echo ==========================================================
echo   OTOPARK ANPR SISTEMI - OTOMATIK KURULUM
echo ==========================================================
echo.

set "PYTHON_CMD="
py -3.12 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"

if defined PYTHON_CMD goto python_ready

echo [1/6] Python 3.12 bulunamadi. Kuruluyor...
where winget >nul 2>nul
if errorlevel 1 goto download_python
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
goto find_python

:download_python
echo Python 3.12 indiriliyor...
set "PY_INSTALLER=%TEMP%\otopark-python-3.12.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%PY_INSTALLER%'"
if errorlevel 1 goto python_error
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1

:find_python
py -3.12 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if defined PYTHON_CMD goto python_ready
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON_CMD goto python_error

:python_ready
echo [1/6] Python hazir:
%PYTHON_CMD% --version
if errorlevel 1 goto python_error

echo [2/6] Sanal ortam kontrol ediliyor...
if not exist "%VENV_DIR%\Scripts\python.exe" goto create_venv
if not exist "%VENV_DIR%\Scripts\pythonw.exe" goto repair_venv
"%VENV_DIR%\Scripts\python.exe" --version >nul 2>nul
if not errorlevel 1 goto venv_ready
:repair_venv
ren "%VENV_DIR%" ".venv_eski_%RANDOM%"

:create_venv
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 goto venv_error

:venv_ready
"%VENV_DIR%\Scripts\python.exe" -c "import hashlib,pathlib,sys; m=pathlib.Path(r'%VENV_DIR%\requirements.sha256'); h=hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest(); sys.exit(0 if m.is_file() and m.read_text(encoding='ascii').strip()==h else 1)" >nul 2>nul
if errorlevel 1 goto install_packages
"%VENV_DIR%\Scripts\python.exe" -m pip check >nul 2>nul
if not errorlevel 1 goto packages_ready

:install_packages
echo [3/6] Paket yoneticisi hazirlaniyor...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto package_error

echo [4/6] Program kutuphaneleri kuruluyor...
echo Bu islem internet hizina gore 5-15 dakika surebilir.
echo Eski ve artik kullanilmayan agir OCR paketleri temizleniyor...
"%VENV_DIR%\Scripts\python.exe" -m pip uninstall -y easyocr torch torchvision opencv-python >nul 2>nul
"%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto package_error
"%VENV_DIR%\Scripts\python.exe" -m pip check
if errorlevel 1 goto package_error
"%VENV_DIR%\Scripts\python.exe" -c "import hashlib,pathlib; pathlib.Path(r'%VENV_DIR%\requirements.sha256').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest(), encoding='ascii')"
if errorlevel 1 goto package_error

:packages_ready
echo [4/6] Program kutuphaneleri hazir.

echo Mevcut veriler guvenceye aliniyor...
"%VENV_DIR%\Scripts\python.exe" -m app.backup
if errorlevel 1 goto backup_error

set "MODEL_MARKER=%LocalAppData%\OtoparkANPRPublic\data\.fastalpr-0.4.0-hazir"
if exist "%MODEL_MARKER%" goto model_ready
echo Plaka tespit ve OCR modelleri ilk kurulum icin indiriliyor ve hazirlaniyor...
echo Bu adim internet hizina gore uzun surebilir; pencereyi kapatmayin.
"%VENV_DIR%\Scripts\python.exe" -c "from pathlib import Path; from app.anpr.fast_engine import get_engine; get_engine(); p=Path(r'%MODEL_MARKER%'); p.parent.mkdir(parents=True, exist_ok=True); p.touch(); print('Plaka tanima modelleri hazir.')"
if errorlevel 1 goto model_error
:model_ready

echo [5/6] Ayarlar ve veritabani hazirlaniyor...
if not exist ".env" copy ".env.example" ".env" >nul
"%VENV_DIR%\Scripts\python.exe" -c "from app import db; db.init_db()"
if errorlevel 1 goto database_error

echo [6/6] Program kontrol ediliyor...
"%VENV_DIR%\Scripts\python.exe" -c "from app.ui.main_window import MainWindow; print('Program dosyalari hazir.')"
if errorlevel 1 goto verify_error

echo.
echo ==========================================================
echo   KURULUM BASARIYLA TAMAMLANDI
echo ==========================================================
echo Programi acmak icin run.bat dosyasina cift tiklayin.
echo Ilk giris: admin / admin
echo.
pause
exit /b 0

:python_error
echo [HATA] Python 3.12 kurulamadi. Interneti kontrol edip tekrar deneyin.
goto failed
:venv_error
echo [HATA] Sanal ortam olusturulamadi.
goto failed
:package_error
echo [HATA] Kutuphaneler kurulamadi. Internet baglantisini kontrol edin.
goto failed
:model_error
echo [HATA] Plaka tanima modeli indirilemedi. Interneti kontrol edin.
goto failed
:database_error
echo [HATA] Veritabani olusturulamadi.
goto failed
:backup_error
echo [HATA] Veritabani yedegi alinamadi. Veri guvenligi icin kurulum durduruldu.
goto failed
:verify_error
echo [HATA] Program kontrolu basarisiz oldu.
:failed
echo Kurulum tamamlanamadi.
pause
exit /b 1
