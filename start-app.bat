@echo off
cd /d "c:\Users\Reception\Documents\GitHub\biggrin-booking-system"
python -m pip install -q -r requirements.txt 2>nul
python app.py
