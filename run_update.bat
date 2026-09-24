@echo off
cd /d "%~dp0"
py -3 -m stockrec update --interval 1d --interval 1m --interval 5m
