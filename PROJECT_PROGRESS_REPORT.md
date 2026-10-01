# PROJECT PROGRESS & TECHNICAL SPECIFICATION REPORT

---

## SMART CLASSROOM ATTENDANCE SYSTEM USING COMPUTER VISION & DEEP LEARNING

**Academic Year:** 2025 – 2026  
**Document Type:** Project Progress & Technical Design Report  
**Target Audience:** Project Guide / Review Committee  
**Version:** 2.0 (Modernized Release)  
**Repository Branch:** `main` (Commit `ad6d443`)  

---

## 1. ABSTRACT & PROJECT OVERVIEW

Traditional attendance tracking in educational institutions relies on manual roll calls or paper-based sign-in sheets—processes that are time-consuming, prone to human error, susceptible to proxy attendance, and disruptive to lecture hours.

This project delivers a **Smart AI-Powered Face Attendance System** engineered with **Django (Python)**, **OpenCV**, **dlib**, and deep convolutional metric learning via `face_recognition`. The system automates attendance logging through two distinct operating modes:
1. **Classroom Group Photo Mode (Batch Processing):** The instructor captures a single wide-angle photograph of the lecture hall. The deep learning pipeline automatically detects all faces, computes 128-dimensional facial embeddings, matches them against enrolled students in that specific section, and presents an interactive review table with confidence metrics for quick teacher verification before database commit.
2. **Live Webcam Scanner Mode (Real-Time Ingestion):** A real-time web-based camera stream processes video frames asynchronously, continuously recognizing students present in front of the camera and updating attendance counts dynamically.

Additionally, the system eliminates the bottleneck of manual student registration by integrating **Bulk Excel Ingestion** with **Automated Google Drive Photo Caching**, allowing hundreds of student profiles to be provisioned seamlessly.

---

## 2. PROBLEM STATEMENT & OBJECTIVES

### 2.1 Problem Statement
- **Time Inefficiency:** Manual roll-calling for classes of 60–100 students consumes 10–15% of instructional time.
- **Proxy Attendance:** Paper sign-ins allow students to mark attendance for absent peers.
- **Data Fragmentation:** Paper records or disconnected spreadsheets make attendance analytics, statutory compliance, and detention tracking tedious.

### 2.2 Core Project Objectives
1. Develop a high-accuracy, multi-face recognition pipeline capable of identifying students in varied lighting and classroom seating arrangements.
2. Provide a **Human-in-the-Loop** verification interface enabling instructors to confirm or override AI predictions in seconds.
3. Enable automated bulk student provisioning via Excel spreadsheets with direct Google Drive photo extraction.
4. Deliver an intuitive, responsive web portal equipped with role-based access control, analytics cards, and audit logs.

---

## 3. EXISTING SYSTEM VS. PROPOSED SYSTEM

| Parameter | Legacy / Existing Approaches | Proposed Smart Attendance System |
| :--- | :--- | :--- |
| **Data Collection** | Manual roll-calling / Biometric fingerprint scanners | Multi-face classroom photo OR Real-time browser webcam |
| **Hardware Overhead** | Expensive dedicated fingerprint/RFID hardware | Standard commodity smartphone/webcam camera |
| **Hygiene / Contact** | Contact-based fingerprinting (unsanitary) | 100% Non-contact, passive computer vision |
| **Detection Engine** | Basic Haar Cascades / LBPH (high false positives) | 128D Deep ResNet metric embeddings via dlib |
| **Batch Size** | 1 face at a time (queues required) | Simultaneous multi-face detection (entire classroom) |
| **Review Capability** | None (blind write to DB) | Interactive 1-click toggle review before finalizing |
| **Student Ingestion** | Manual form entry one student at a time | Automated Excel batch import with Google Drive photo fetching |
| **Audit & Editing** | Paper corrections or direct DB modifications | Dedicated web interface for historical attendance editing |

---

## 4. SYSTEM ARCHITECTURE

```
+-----------------------------------------------------------------------------------+
|                                  USER INTERFACE                                   |
|  - Faculty Login / Registration     - Classroom Photo Uploader / Interactive Roster |
|  - Real-time Webcam Scanner         - Student Directory & Excel Bulk Uploader      |
+------------------------------------------+----------------------------------------+
                                           | HTTP / AJAX (JSON + Base64)
                                           v
+-----------------------------------------------------------------------------------+
|                            DJANGO WEB APPLICATION (CORE)                          |
|  +--------------------+   +-----------------------+   +------------------------+  |
|  |   View Handlers    |   | Authentication & Auth |   | Attendance Controller  |  |
|  | (views.py, urls.py)|   | (Django Auth System)  |   | (Session & Roster Sync)|  |
|  +---------+----------+   +-----------+-----------+   +-----------+------------+  |
+------------|--------------------------|---------------------------|---------------+
             |                          |                           |
             v                          v                           v
+------------------------+  +-----------------------+  +----------------------------+
|  AI RECOGNITION ENGINE |  | CLOUD / DRIVE SERVICE |  |     DATABASE (SQLite3)     |
| - OpenCV Frame Reader  |  | - Drive URL Parser    |  | - Student & Faculty Models |
| - HOG / CNN Detector   |  | - Direct Image Downl. |  | - AttendanceSession Model  |
| - 128D Vector Embedder |  | - OpenPyXL Excel Read |  | - Attendence Record Model  |
| - Euclidean Dist Match |  | - Local Disk Caching  |  | - FaceEmbedding Vector Tab |
+------------------------+  +-----------------------+  +----------------------------+
```

---

## 5. TECHNICAL SPECIFICATIONS

### 5.1 Software Requirements
- **Operating System:** Windows 10/11, Ubuntu 20.04+, or macOS
- **Programming Language:** Python 3.10 – 3.13
- **Web Framework:** Django 6.1 (MTV Architecture)
- **Computer Vision & ML Libraries:**
  - `face_recognition` (v1.3+)
  - `dlib` (v19.8+)
  - `opencv-python` (v4.10+)
  - `numpy`
- **Data & Excel Processing:** `openpyxl`, `pandas`
- **Frontend Stack:** HTML5, CSS3, JavaScript (ES6), Bootstrap 4.6, FontAwesome 6, Plus Jakarta Sans

### 5.2 Hardware Requirements (Minimum Recommended)
- **Processor:** Intel Core i5 (8th Gen or higher) / AMD Ryzen 5
- **RAM:** 8 GB minimum (16 GB recommended for batch image processing)
- **Camera:** 1080p (Full HD) webcam or standard smartphone camera (12MP+) for classroom group shots
- **Storage:** 2 GB free disk space for dependencies and student photo storage

---

## 6. MODULE-WISE IMPLEMENTATION DETAILS

### 6.1 Module 1: Deep Learning Face Recognition Engine
- **Face Localization:** Utilizes Histogram of Oriented Gradients (HOG) combined with linear SVM for fast real-time CPU detection, with optional CNN model support for high-density group photos.
- **Landmark Alignment:** 68 facial landmarks are computed using dlib's shape predictor to align the face horizontally and normalize eye/nose tilt.
- **Feature Vector Extraction:** An affine-transformed crop of each detected face is passed through a pre-trained deep ResNet network to output a normalized **128-dimensional embedding vector**.
- **Distance Metric & Matching:** 
  $$\text{Euclidean Distance: } d(u, v) = \sqrt{\sum_{i=1}^{128} (u_i - v_i)^2}$$
  - A threshold of $d \le 0.50 - 0.55$ defines a verified identity match.
  - Confidence percentage is computed dynamically:
    $$\text{Confidence} = \text{round}((1.0 - d) \times 100, 1)\%$$
- **Vector Caching:** To avoid running expensive ResNet feature extraction on hundreds of reference photos during every attendance run, face embeddings are serialized and stored in the `FaceEmbedding` table.

### 6.2 Module 2: Classroom Batch Attendance Mode
1. The teacher selects **Branch**, **Year**, **Section**, and **Subject**.
2. An image of the seated classroom is uploaded.
3. The system pulls the enrolled student roster for that specific section.
4. The recognition engine finds all faces, matches them against the roster, and generates an annotated output image highlighting each detected student with bounding boxes.
5. **Interactive Review:** An intuitive web table lists all students in the class with their detected status (Present / Absent) and recognition confidence.
6. The teacher can toggle any status with one click and click **"Save Attendance"**, which commits records to `AttendanceSession` and `Attendence`.

### 6.3 Module 3: Real-Time Live Webcam Scanner
- Utilizes the browser's `navigator.mediaDevices.getUserMedia` API to stream video at 30 fps.
- Video frames are captured to an off-screen HTML5 Canvas and transmitted to the Django backend (`/attendance/live-scan/`) via asynchronous `POST` requests.
- The backend matches recognized faces in the frame against the section roster and returns recognition results with confidence values.
- Real-time HUD elements in the browser display detected student names, timestamped logs, and a dynamic count of Present vs. Total students.
- A **"Save Live Session"** button locks and persists the live attendance log to the database.

### 6.4 Module 4: Cloud Ingestion (Google Drive & Excel Bulk Import)
- **Excel Spreadsheet Parser:** Instructors upload an `.xlsx` file containing:
  - Register Number / Registration ID
  - Full Name / First Name / Last Name
  - Branch, Year, Section
  - Email & Phone
  - Google Drive Shareable Link to student profile photo
- **Automated Drive Fetcher (`google_drive_helper.py`):**
  - Parses multiple Google Drive URL formats (e.g., `drive.google.com/file/d/<id>`, `drive.google.com/open?id=<id>`, `uc?id=<id>`).
  - Downloads the image directly through Google's streaming endpoint with anti-throttling headers.
  - Automatically converts and saves the image to `static/images/Student_Images/<branch>/<year>/<section>/<reg_no>.jpg`.
  - Computes and caches the student's 128D facial embedding immediately upon download.

### 6.5 Module 5: Student Directory & Management
- Paginated, searchable directory of enrolled students.
- Live search by registration number, name, or branch.
- Modals for viewing student photo cards, updating profiles, individual student deletion, and bulk selection deletion.
- Downloadable Excel template (`/students/template/export/`) so administrators have a pre-formatted template for uploading new classes.

### 6.6 Module 6: Authentication, Faculty Profiles & Security
- Complete user registration and login management with Django's cryptographic password hashing (PBKDF2 with SHA-256).
- One-to-one mapped `Faculty` profile model storing contact information and profile picture.
- Self-service password change and password reset interfaces.
- Route protection with `@login_required` to safeguard sensitive student attendance records.

---

## 7. DATABASE SCHEMA & DATA DICTIONARY

### 7.1 Student (`attendence_sys_student`)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | AutoField (PK) | Primary Key |
| `register_number` | CharField(200) (Unique) | University registration / Roll number |
| `name` | CharField(200) | Full student name |
| `department` / `branch`| CharField(100) | Academic branch (CSE, IT, ECE, MECH, etc.) |
| `year` | CharField(100) | Year of study (1, 2, 3, 4) |
| `section` | CharField(100) | Class section (A, B, C) |
| `profile_pic` | ImageField | Path to student face reference photo |

### 7.2 AttendanceSession (`attendence_sys_attendancesession`)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | AutoField (PK) | Primary Key |
| `date` | DateField | Date of lecture session |
| `department` | CharField(100) | Branch |
| `year` / `section` | CharField(100) | Class Year & Section |
| `subject` | CharField(200) | Course title / Subject name |
| `teacher` | CharField(200) | Faculty conducting the session |
| `classroom_image` | ImageField | Uploaded classroom group picture |
| `created_at` | DateTimeField | Timestamp of session creation |

### 7.3 Attendence (`attendence_sys_attendence`)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | AutoField (PK) | Primary Key |
| `session_id` | ForeignKey (AttendanceSession) | Linked session |
| `student_ref_id` | ForeignKey (Student) | Linked student record |
| `Student_ID` | CharField(200) | Registration number for reporting |
| `date` / `time` | DateField / TimeField | Date and timestamp of record |
| `status` | CharField(200) | `'Present'` or `'Absent'` |
| `confidence` | FloatField | AI recognition confidence percentage (e.g., 88.5%) |

### 7.4 FaceEmbedding (`attendence_sys_faceembedding`)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | AutoField (PK) | Primary Key |
| `student_id` | ForeignKey (Student) | Linked student |
| `embedding` | TextField | JSON serialized 128-dimensional vector |
| `model_name` | CharField(100) | Encoding model identifier |

---

## 8. SUMMARY OF COMPLETED MILESTONES

| # | Milestone / Task | Status | Details |
| :-: | :--- | :-: | :--- |
| **1** | Deep Learning Facial Recognition Engine | **Completed** | 128D embedding matching with confidence scores & visual bounding boxes |
| **2** | Dual Input Modalities | **Completed** | Both classroom photo upload and real-time live webcam supported |
| **3** | Interactive Human-in-the-Loop Review | **Completed** | Editable verification roster before saving attendance |
| **4** | Google Drive & Excel Bulk Ingestion | **Completed** | Automated photo downloading and bulk student account provisioning |
| **5** | Historical Search & Analytics Dashboard | **Completed** | Date/branch/section search, statistics KPI cards |
| **6** | Attendance Record Modification | **Completed** | Dedicated post-session edit interface |
| **7** | Full Authentication & Faculty Profiles | **Completed** | Login, registration, password reset, and profile management |
| **8** | Version Control & Repository Synchronization | **Completed** | Clean Git repository on `main` branch, pushed to GitHub remote |

---

## 9. CONCLUSION & FUTURE SCOPE

The system successfully resolves the latency and proxy vulnerabilities inherent in traditional attendance marking. By combining automated deep learning recognition with faculty verification controls and cloud ingestion, the application offers an efficient and practical tool for modern classrooms.

### Recommended Next Steps for Final Project Submission:
1. **Liveness Detection (Anti-Spoofing):** Add eye-blink or head-movement detection in the live webcam scanner to prevent photo/screen spoofing.
2. **Automated Notification Dispatch:** Integrate automated email or SMS notifications to parents or students when attendance falls below institutional thresholds (e.g., 75%).
3. **Automated Report Export:** Provide one-click PDF and CSV export for monthly department attendance reports.

---
*Report generated and maintained in the project root directory as `PROJECT_PROGRESS_REPORT.md`.*
