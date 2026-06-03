@echo off
chcp 65001 >nul
cd /d %~dp0
REM 用 pythonw 无黑窗口运行,只在状态栏(系统托盘)显示小图标
start "" pythonw wake_doubao_xfei.py
echo 已启动豆包语音唤醒,请看屏幕右下角状态栏的小图标。
echo 退出: 右键托盘小图标 -> 退出。
echo 开机自启: 右键托盘小图标 -> 勾选"开机自启"即可(无需手动操作)。
timeout /t 4 >nul
