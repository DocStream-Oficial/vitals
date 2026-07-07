@echo off
cd /d C:\vitals-app
C:\vitals-app\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8700
