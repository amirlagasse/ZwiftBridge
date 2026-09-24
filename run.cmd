@echo off
rem Double-clickable wrapper around run.ps1, so the panel opens from Explorer
rem or the Start Menu without a PowerShell execution-policy prompt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
