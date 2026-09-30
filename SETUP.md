# Face Verification Based Attendance System - Setup Guide (Windows)

This document provides exact step-by-step Windows commands to set up, synchronize, and run the Django Attendance System.

---

## 1. Prerequisites
- **Python 3.10+** (Tested on Python 3.13)
- **Git**

---

## 2. Opening the Project
Open PowerShell or Command Prompt, then navigate to your project folder:

```powershell
cd d:\iitdmk
```

---

## 3. Creating Virtual Environment
Create a virtual environment named `venv`:

```powershell
python -m venv venv
```

---

## 4. Activating Virtual Environment
Activate the virtual environment on Windows:

### PowerShell:
```powershell
.\venv\Scripts\Activate.ps1
```
*(If PowerShell blocks script execution, run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process`)*

### Command Prompt (cmd.exe):
```cmd
venv\Scripts\activate.bat
```

---

## 5. Installing Dependencies
Install all required project dependencies:

```powershell
pip install django django-filter pillow opencv-python google-api-python-client google-auth-httplib2 google-auth-oauthlib requests
```

---

## 6. Running Database Migrations
Apply database schema migrations:

```powershell
python manage.py makemigrations
python manage.py migrate
```

---

## 7. Creating Superuser (Admin Access)
Create an admin account to log into the Django Admin dashboard:

```powershell
python manage.py createsuperuser
```
Follow the prompts to enter your desired username, email, and password.

---

## 8. Synchronizing Student Data from Google Sheet
The Google Sheet connected to your Google Form is the **Source of Truth** for student registration.

### Standard Synchronization Command:
```powershell
python manage.py sync_google_sheet "YOUR_GOOGLE_SHEET_URL_OR_ID"
```

#### Example using a Google Sheet URL:
```powershell
python manage.py sync_google_sheet "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"
```

#### Example using a local exported CSV file:
```powershell
python manage.py sync_google_sheet "path\to\student_responses.csv"
```

---

## 9. Starting the Development Server
Start the local Django web server:

```powershell
python manage.py runserver
```

---

## 10. Opening the Application in Browser
- **Main Attendance Portal**: Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Django Admin Panel**: Open [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
- **Student Profile Photo Endpoint**: Open `http://127.0.0.1:8000/students/<student_id>/photo/`

---

## Features & Architecture Overview
- **Google Sheet Source of Truth**: Dynamically maps header columns (`Register Number`, `Name`, `Department`, `Year`, `Section`, `Email`, `Phone`, `Photo 1`, `Photo 2`, `Photo 3`).
- **Google Drive File ID Extraction**: Automatically extracts drive file IDs from any Drive URL format.
- **Photo 1 Primary Profile**: Photo 1 is automatically designated as `is_primary=True` for profile display.
- **Secure Image Proxying**: Downloads and serves Google Drive images via backend endpoint without exposing sensitive credentials to the browser.
