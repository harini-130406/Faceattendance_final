# Face Attendance System

A modern, AI-powered Face Recognition Attendance System built with Django, OpenCV, dlib, and `face_recognition`.

## Key Features
- **AI Classroom Attendance**: Upload a classroom group photo, automatically detect faces, match against enrolled students with confidence scoring, and visually preview detection bounding boxes.
- **Interactive Review & Manual Editing**: Real-time review table to toggle Present/Absent with 1-click status updates before and after saving.
- **Google Drive & Excel Integration**: Bulk import students from Excel spreadsheets with automatic download and caching of student photos from Google Drive sharing links.
- **Student Directory**: Complete student management with photo previews, search, filtering by department/year/section, and individual/bulk deletion.
- **Attendance Analytics & History**: Multi-parameter search and reporting by date, period, branch, and status.
- **Modern Dashboard**: Responsive UI with dark navbar, KPI metric cards, and clean typography.

## Tech Stack
- **Backend**: Django 6.1, Python 3.13
- **Face Recognition**: `face_recognition`, `dlib-bin`, `opencv-python`
- **Frontend**: Bootstrap 4.6, Plus Jakarta Sans, FontAwesome 6
- **Database**: SQLite3 (compatible with PostgreSQL/MySQL)

## Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/harini-130406/Faceattendance_final.git
   cd Faceattendance_final
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run Database Migrations**:
   ```bash
   python manage.py migrate
   ```

5. **Create Superuser / Faculty Account**:
   ```bash
   python manage.py createsuperuser
   ```

6. **Start the Development Server**:
   ```bash
   python manage.py runserver
   ```
   Open `http://127.0.0.1:8000/` in your browser.
