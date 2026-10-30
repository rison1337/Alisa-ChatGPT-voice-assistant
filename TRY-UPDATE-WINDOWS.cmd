@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "ALISA_TEST_ARCHIVE=%~dp0archives\alisa-voice-windows.zip"
if not exist "%ALISA_TEST_ARCHIVE%" set "ALISA_TEST_ARCHIVE=%~dp0alisa-voice-windows.zip"
if not exist "%ALISA_TEST_ARCHIVE%" (
  echo Рядом нужен alisa-voice-windows.zip и соседний файл .sha256.
  exit /b 1
)
if not exist "%LOCALAPPDATA%\AlisaVoice\alisa.cmd" (
  echo Сначала нужен установленный Windows CLI предыдущей версии.
  echo Для новой установки используйте package\START-WINDOWS.cmd.
  exit /b 1
)
call "%LOCALAPPDATA%\AlisaVoice\alisa.cmd" update --archive "%ALISA_TEST_ARCHIVE%" --version 0.3.0.dev2
exit /b %errorlevel%
