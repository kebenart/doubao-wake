@echo off
chcp 65001 >nul
cd /d %~dp0
title 豆包语音唤醒
echo 启动中... 看到 "讯飞唤醒已启动" 后,喊 "豆包豆包" 试试。关闭本窗口即停止。
python wake_doubao_xfei.py
pause
