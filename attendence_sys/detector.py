import base64
import json
import os
import cv2
import numpy as np
try:
    import face_recognition
except ImportError:
    face_recognition = None

from django.conf import settings
from .models import Student, StudentPhoto, FaceEmbedding


# Module-level memory cache for student face encodings to avoid repeated DB/file access
_ENCODINGS_CACHE = {}


def get_student_face_encoding(student):
    """
    Returns the 128-d face encoding for a student as a numpy array.
    Uses in-memory cache, then cached FaceEmbedding; otherwise computes from profile photo and caches it.
    """
    if student.id in _ENCODINGS_CACHE:
        return _ENCODINGS_CACHE[student.id]

    # 1. Try cached embedding from DB
    cached = student.embeddings.first()
    if cached and cached.embedding:
        try:
            arr = np.array(json.loads(cached.embedding), dtype=np.float64)
            _ENCODINGS_CACHE[student.id] = arr
            return arr
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
                        _ENCODINGS_CACHE[student.id] = encs[0]
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
            _ENCODINGS_CACHE[student.id] = encs[0]
            return encs[0]
    except Exception as e:
        print(f"[Warning] Could not extract face encoding for student {student}: {e}")

    return None


def _decode_image_source(source):
    """
    Decodes an image source (file-like object, raw bytes, or filesystem path)
    into a BGR OpenCV numpy array.
    """
    if hasattr(source, 'read'):
        source.seek(0)
        file_bytes = source.read()
        nparr = np.frombuffer(file_bytes, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    elif isinstance(source, (bytes, bytearray)):
        nparr = np.frombuffer(source, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    elif isinstance(source, str) and os.path.exists(source):
        img_bgr = cv2.imread(source)
    else:
        return None
    return img_bgr


def process_multiple_classroom_images(classroom_image_sources, branch, year, section, tolerance=0.48):
    """
    Scans and detects faces across one or MULTIPLE classroom photos with calibrated accuracy:
    1. Downscales very large images to optimal working resolution (max 1000px).
    2. Uses fast 5-point landmark alignment & dlib face chips to compute descriptors in pure C++ batch.
    3. Bulk-prefetches student embeddings in a single DB query with in-memory caching.
    4. Optimal 1-to-1 bipartite Euclidean matching per photograph: prevents multiple faces claiming the same student.
    5. Calibrated tolerance (default 0.48) to eliminate false positives in large group photos.
    6. Multi-photo union: detects students across Photo #1, Photo #2, etc., and marks as Present for the session.
    """
    import dlib
    from face_recognition.api import _raw_face_landmarks, face_encoder

    try:
        tolerance = float(tolerance)
    except (ValueError, TypeError):
        tolerance = 0.48

    if not isinstance(classroom_image_sources, (list, tuple)):
        classroom_image_sources = [classroom_image_sources]

    # Filter out empty or None sources
    valid_sources = [s for s in classroom_image_sources if s]
    if not valid_sources:
        raise ValueError("No valid classroom image sources provided.")

    # 1. Fetch all enrolled students for this class
    students = list(Student.objects.filter(
        branch__iexact=branch,
        year=str(year),
        section__iexact=section
    ).order_by('register_number', 'registration_id'))

    if not students:
        students = list(Student.objects.filter(
            department__iexact=branch,
            year=str(year),
            section__iexact=section
        ).order_by('register_number', 'registration_id'))

    # Bulk fetch cached DB embeddings to avoid N+1 queries
    existing_embs = FaceEmbedding.objects.filter(student__in=students)
    for emb in existing_embs:
        if emb.student_id not in _ENCODINGS_CACHE and emb.embedding:
            try:
                _ENCODINGS_CACHE[emb.student_id] = np.array(json.loads(emb.embedding), dtype=np.float64)
            except Exception:
                pass

    # Build reference encodings map
    student_encodings_map = {}
    known_student_ids = []
    known_encodings_list = []

    for student in students:
        enc = get_student_face_encoding(student)
        student_encodings_map[student.id] = {
            'student': student,
            'encoding': enc,
            'name': str(student.name or student.firstname or student.registration_id or student.register_number),
            'has_photo': bool(student.profile_pic.name != '' or student.photos.exists()),
            'photo_url': student.get_primary_photo_url(),
        }
        if enc is not None:
            known_student_ids.append(student.id)
            known_encodings_list.append(enc)

    known_encodings_matrix = np.array(known_encodings_list) if known_encodings_list else None

    # Tracking student presence across all images
    overall_matches = {
        s.id: {
            'matched': False,
            'confidences': [],
            'detected_photos': [],
        }
        for s in students
    }

    # 2. Phase 1: Rapid Face Detection & Aligned Chip Extraction across all photos
    photos_data = []
    all_face_chips = []
    # chip_index -> (photo_idx, face_idx_in_photo)
    chip_to_photo_face = []

    total_detected_faces = 0

    for photo_idx, src in enumerate(valid_sources):
        img_bgr = _decode_image_source(src)
        if img_bgr is None:
            continue

        # Optimal working resolution (1400px preserves face details for back rows while maintaining fast detection)
        h, w = img_bgr.shape[:2]
        max_dim = 1400
        if max(h, w) > max_dim:
            scale = max_dim / float(max(h, w))
            img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

        # HOG face detection with upsampling=1 to capture smaller and further-back classroom faces
        detected_locations = face_recognition.face_locations(img_rgb, model='hog', number_of_times_to_upsample=1)
        # Filter out sub-20px noise artifacts
        detected_locations = [loc for loc in detected_locations if (loc[2] - loc[0]) >= 20 and (loc[1] - loc[3]) >= 20]
        total_detected_faces += len(detected_locations)

        # Fast 5-point landmark alignment & 150x150 chip extraction
        if detected_locations:
            raw_landmarks = _raw_face_landmarks(img_rgb, detected_locations, 'small')
            dlib_dets = dlib.full_object_detections()
            for lm in raw_landmarks:
                dlib_dets.append(lm)
            chips = list(dlib.get_face_chips(img_rgb, dlib_dets, size=150, padding=0.25))
            for f_idx, chip in enumerate(chips):
                all_face_chips.append(chip)
                chip_to_photo_face.append((photo_idx, f_idx))
        else:
            chips = []

        photos_data.append({
            'index': photo_idx + 1,
            'bgr': img_bgr,
            'locations': detected_locations,
            'num_faces': len(detected_locations),
            'face_matches': {},  # face_idx -> match_info
            'matched_student_ids': set(),
        })

    # 3. Phase 2: High-Speed C++ Batch Face Descriptor Computation
    if all_face_chips:
        batch_descriptors = face_encoder.compute_face_descriptor(all_face_chips)
        all_face_encodings = [np.array(d) for d in batch_descriptors]
    else:
        all_face_encodings = []

    # 4. Phase 3: Calibrated 1-to-1 Bipartite Matching with Margin Filtering
    photo_encodings_map = {p_idx: {} for p_idx in range(len(photos_data))}
    for chip_idx, face_enc in enumerate(all_face_encodings):
        p_idx, f_idx = chip_to_photo_face[chip_idx]
        photo_encodings_map[p_idx][f_idx] = face_enc

    num_photos = len(photos_data)
    if known_encodings_matrix is not None and len(known_encodings_matrix) > 0:
        for p_idx, photo_info in enumerate(photos_data):
            f_encs_dict = photo_encodings_map.get(p_idx, {})
            if not f_encs_dict:
                continue

            # Find candidate matches below tolerance, verifying distinctiveness
            candidate_pairs = []
            for f_idx, face_enc in f_encs_dict.items():
                dists = np.linalg.norm(known_encodings_matrix - face_enc, axis=1)
                sorted_idx = np.argsort(dists)
                best_idx = sorted_idx[0]
                best_d = float(dists[best_idx])
                second_d = float(dists[sorted_idx[1]]) if len(sorted_idx) > 1 else 1.0
                margin = second_d - best_d

                # Stricter threshold if ambiguous between two enrolled students
                effective_tolerance = tolerance if margin >= 0.015 else (tolerance - 0.02)

                if best_d <= effective_tolerance:
                    candidate_pairs.append((f_idx, best_idx, best_d, margin))

            # Sort candidate pairs by distance ascending (closest match gets first priority)
            candidate_pairs.sort(key=lambda x: x[2])

            assigned_faces = set()
            assigned_students = set()

            for f_idx, s_arr_idx, d, margin in candidate_pairs:
                if f_idx in assigned_faces or s_arr_idx in assigned_students:
                    continue

                matched_student_id = known_student_ids[s_arr_idx]
                student_meta = student_encodings_map[matched_student_id]
                conf = round(max(0.0, (1.0 - d) * 100), 1)

                assigned_faces.add(f_idx)
                assigned_students.add(s_arr_idx)

                photo_info['face_matches'][f_idx] = {
                    'student_id': matched_student_id,
                    'name': student_meta['name'],
                    'dist': d,
                    'conf': conf,
                    'margin': margin,
                }

    # Aggregate presence across all photos and annotate images
    annotated_images = []

    for photo_info in photos_data:
        p_idx = photo_info['index']
        photo_label = f"Photo #{p_idx}"
        bgr = photo_info['bgr']
        locs = photo_info['locations']
        face_matches = photo_info['face_matches']

        # Update overall matches
        for f_idx, m_info in face_matches.items():
            s_id = m_info['student_id']
            overall_matches[s_id]['matched'] = True
            overall_matches[s_id]['confidences'].append(m_info['conf'])
            if photo_label not in overall_matches[s_id]['detected_photos']:
                overall_matches[s_id]['detected_photos'].append(photo_label)
            photo_info['matched_student_ids'].add(s_id)

        # Draw bounding boxes
        annotated = bgr.copy()
        for f_idx, (top, right, bottom, left) in enumerate(locs):
            if f_idx in face_matches:
                m_info = face_matches[f_idx]
                label = f"{m_info['name']} ({round((1.0 - m_info['dist']) * 100)}%)"
                color = (46, 204, 113)  # Emerald green for recognized
            else:
                label = "Unassigned Face"
                color = (241, 196, 15)  # Amber for unassigned

            cv2.rectangle(annotated, (left, top), (right, bottom), color, 2)
            label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            label_w, label_h = label_size
            cv2.rectangle(annotated, (left, max(0, top - 22)), (left + label_w + 8, top), color, -1)
            cv2.putText(
                annotated,
                label,
                (left + 4, max(15, top - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.46,
                (0, 0, 0),
                1,
                cv2.LINE_AA
            )

        # Encode to lightweight base64 JPEG (Quality 75 for fast transmission)
        _, buffer = cv2.imencode('.jpg', annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        annotated_b64 = f"data:image/jpeg;base64,{base64.b64encode(buffer).decode('utf-8')}"

        annotated_images.append({
            'index': p_idx,
            'label': f"Classroom Photo #{p_idx}",
            'detected_faces': len(locs),
            'recognized_count': len(photo_info['matched_student_ids']),
            'image_b64': annotated_b64,
        })

    # 5. Build final roster with multi-photo accuracy boosting
    student_results = []
    for student in students:
        s_data = student_encodings_map[student.id]
        m_info = overall_matches[student.id]
        is_present = m_info['matched']
        photos_seen = len(m_info['detected_photos'])

        if is_present and m_info['confidences']:
            base_conf = max(m_info['confidences'])
            if photos_seen >= 2:
                # Multi-photo consensus bonus: reinforce confidence by +5%
                final_conf = min(99.0, round(base_conf + 5.0, 1))
                dual_confirmed = True
            else:
                final_conf = base_conf
                dual_confirmed = False
            detected_str = ", ".join(m_info['detected_photos'])
        else:
            final_conf = 0.0
            dual_confirmed = False
            detected_str = None

        student_results.append({
            'student': student,
            'status': 'Present' if is_present else 'Absent',
            'confidence': final_conf,
            'dual_confirmed': dual_confirmed,
            'photos_seen_count': photos_seen,
            'detected_in': detected_str,
            'detected_photos_list': m_info['detected_photos'],
            'has_photo': s_data['has_photo'],
            'photo_url': s_data['photo_url'],
        })

    present_count = sum(1 for r in student_results if r['status'] == 'Present')
    absent_count = len(student_results) - present_count
    attendance_rate = round((present_count / len(student_results) * 100), 1) if student_results else 0.0

    return {
        'total_enrolled': len(student_results),
        'total_photos': len(annotated_images),
        'total_detected_faces': total_detected_faces,
        'present_count': present_count,
        'absent_count': absent_count,
        'attendance_rate': attendance_rate,
        'annotated_images': annotated_images,
        'annotated_image': annotated_images[0]['image_b64'] if annotated_images else None,
        'roster': student_results,
    }


def process_classroom_image(classroom_image_source, branch, year, section, tolerance=0.48):
    """
    Backwards-compatible wrapper: calls process_multiple_classroom_images with a single source.
    """
    return process_multiple_classroom_images(
        [classroom_image_source],
        branch=branch,
        year=year,
        section=section,
        tolerance=tolerance
    )
