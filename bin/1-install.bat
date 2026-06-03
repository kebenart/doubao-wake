@echo off
chcp 65001 >nul
echo ============================================
echo  豆包语音唤醒 - 安装依赖
echo ============================================
echo.
echo 正在安装 Python 依赖 (keyboard + 托盘图标 pystray/Pillow)...
echo (录音用 Windows 自带 winmm,无需 sounddevice/cffi)
python -m pip install keyboard pystray Pillow
echo.
echo 安装完成!
echo   - 想看日志/首次调试: 运行 "2-run.bat"(带黑窗口)
echo   - 平时用: 运行 "3-run-background.bat"(只在状态栏显示小图标,右键可退出)
echo.
pause
