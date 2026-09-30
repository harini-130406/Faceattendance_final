import base64
import json
import os
import cv2
import numpy as np
import face_recognition

from django.conf import settings
from .models import Student, StudentPhoto, FaceEmbedding


def get_student_face_encoding(student):
    """
    Returns the 128-d face encoding for a student as a numpy array.
    Uses cached FaceEmbedding if available; otherwise computes from profile photo and caches it.
    """
    # 1. Try cached embedding
    cached = student.embeddings.first()
    if cached and cached.embedding:
        try:
            return np.array(json.loads(cached.embedding), dtype=np.float64)
        except Exception:
            pass

    # 2. Try profile_pic
    img_path = None
    if student.profile_pic and os.path.exists(student.profile_pic.path):
        img_path = student.profile_pic.path
    else:
        # Fallback to local static directory convention
        dept = student.department or student.branch or ''
        yr = student.year or ''
        sec = student.section or ''
        reg = student.register_number or student.registration_id or ''
        for ext in ['jpg', 'png', 'jpeg']:
            p = os.path.join(settings.BASE_DIR, 'static', 'images', 'Student_Images', dept, str(yr), sec, f"{reg}.{ext}")
            if os.path.exists(p):
                img_path = p
                break

    if not img_path or not os.path.exists(img_path):
        # Try primary StudentPhoto cache
        primary = student.get_primary_photo()
        if primary and primary.drive_file_id:
            from .google_drive_helper import fetch_drive_image_bytes
            img_bytes, _ = fetch_drive_image_bytes(primary.drive_file_id)
            if img_bytes:
                nparr = np.frombuffer(img_bytes, np.uint8)
                bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if bgr is not None:
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    encs = face_recognition.face_encodings(rgb)
                    if encs:
                        FaceEmbedding.objects.create(
                            student=student,
                            embedding=json.dumps(encs[0].tolist()),
                            model_name='face_recognition_dlib'
                        )
                        return encs[0]
        return None

    try:
        rgb = face_recognition.load_image_file(img_path)
        encs = face_recognition.face_encodings(rgb)
        if encs:
            FaceEmbedding.objects.create(
                student=student,
                embedding=json.dumps(encs[0].tolist()),
                model_name='face_recognition_dlib'
            )
            return encs[0]
    except Exception as e:
        print(f"[Warning] Could not extract face encoding for student {student}: {e}")

    return None


def process_classroom_image(classroom_image_source, branch, year, section, tolerance=0.55):
    """
    Detects faces in classroom image and matches against enrolled students.
    classroom_image_source can be a file path, bytes, or UploadedFile.
    """
    # 1. Load image into BGR numpy array
    if hasattr(classroom_image_source, 'read'):
        classroom_image_source.seek(0)
        file_bytes = classroom_image_source.read()
        nparr = np.frombuffer(file_bytes, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    elif isinstance(classroom_image_source, (bytes, bytearray)):
        nparr = np.frombuffer(classroom_image_source, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    elif isinstance(classroom_image_source, str) and os.path.exists(classroom_image_source):
        img_bgr = cv2.imread(classroom_image_source)
    else:
        raise ValueError("Invalid classroom image source provided.")

    if img_bgr is None:
        raise ValueError("Failed to decode classroom image.")

    # Resize large images for detection speed while preserving aspect ratio
    h, w = img_bgr.shape[:2]
    max_dim = 1600
    scale = 1.0
    if max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # 2. Detect face locations and encodings
    detected_locations = face_recognition.face_locations(img_rgb, model='hog')
    detected_encodings = face_recognition.face_encodings(img_rgb, detected_locations)

    # 3. Query enrolled students
    students = Student.objects.filter(
        branch__iexact=branch,
        year=str(year),
        section__iexact=section
    ).order_by('register_number', 'registration_id')

    # If no students found with exact match, try department
    if not students.exists():
        students = Student.objects.filter(
            department__iexact=branch,
            year=str(year),
            section__iexact=section
        ).order_by('register_number', 'registration_id')

    # Map face indices to student matches
    face_idx_to_student = {}
    matched_student_ids = set()
    student_results = []

    # 4. Compare each student against classroom detected faces
    for student in students:
        student_enc = get_student_face_encoding(student)
        is_present = False
        confidence = 0.0
        match_idx = None

        if student_enc is not None and len(detected_encodings) > 0:
            distances = face_recognition.face_distance(detected_encodings, student_enc)
            best_idx = int(np.argmin(distances))
            min_dist = float(distances[best_idx])

            if min_dist <= tolerance:
                # If this detected face wasn't already taken by a closer match
                if best_idx not in face_idx_to_student or distances[best_idx] < face_idx_to_student[best_idx]['dist']:
                    is_present = True
                    confidence = round(max(0.0, (1.0 - min_dist) * 100), 1)
                    match_idx = best_idx
                    face_idx_to_student[best_idx] = {
                        'student': student,
                        'dist': min_dist,
                        'name': str(student.name or student.firstname or student.registration_id or student.register_number)
                    }
                    matched_student_ids.add(student.id)

        student_results.append({
            'student': student,
            'status': 'Present' if is_present else 'Absent',
            'confidence': confidence,
            'has_photo': (student.profile_pic.name != '' or student.photos.exists()),
            'photo_url': student.get_primary_photo_url(),
            'match_idx': match_idx,
        })

    # Ensure mutually exclusive matching
    for res in student_results:
        s_id = res['student'].id
        if res['status'] == 'Present' and s_id not in matched_student_ids:
            res['status'] = 'Absent'
            res['confidence'] = 0.0

    # 5. Draw bounding boxes on the image for faculty visual review
    annotated = img_bgr.copy()
    for i, (top, right, bottom, left) in enumerate(detected_locations):
        if i in face_idx_to_student:
            info = face_idx_to_student[i]
            label = f"{info['name']} ({round((1.0 - info['dist'])*100)}%)"
            color = (46, 204, 113) # Emerald Green for recognized Present
        else:
            label = "Unassigned Face"
            color = (241, 196, 15) # Amber for other face

        # Draw box
        cv2.rectangle(annotated, (left, top), (right, bottom), color, 3)

        # Draw label background
        label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        label_w, label_h = label_size
        cv2.rectangle(annotated, (left, max(0, top - 25)), (left + label_w + 10, top), color, -1)
        cv2.putText(
            annotated,
            label,
            (left + 5, max(15, top - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 0),
            1,
            cv2.LINE_AA
        )

    # Convert annotated image to base64
    _, buffer = cv2.imencode('.jpg', annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    annotated_b64 = f"data:image/jpeg;base64,{base64.b64encode(buffer).decode('utf-8')}"

    present_count = sum(1 for r in student_results if r['status'] == 'Present')
    absent_count = len(student_results) - present_count
    attendance_rate = round((present_count / len(student_results) * 100), 1) if student_results else 0.0

    return {
        'total_enrolled': len(student_results),
        'total_detected_faces': len(detected_locations),
        'present_count': present_count,
        'absent_count': absent_count,
        'attendance_rate': attendance_rate,
        'annotated_image': annotated_b64,
        'roster': student_results,
    }
