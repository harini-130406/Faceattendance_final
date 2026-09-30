import os
import re
import requests
from django.conf import settings

CACHE_DIR = os.path.join(settings.MEDIA_ROOT, 'google_drive_cache')

def extract_google_drive_file_id(url_or_id):
    """
    Extracts Google Drive file ID from various Drive URL formats or raw file IDs.
    """
    if not url_or_id:
        return ""
    url_or_id = str(url_or_id).strip()
    
    # Format 1: /file/d/FILE_ID/view or /file/d/FILE_ID
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url_or_id)
    if match:
        return match.group(1)
        
    # Format 2: ?id=FILE_ID or &id=FILE_ID
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url_or_id)
    if match:
        return match.group(1)
        
    # Format 3: Raw ID string (20+ chars containing alphanumeric, dash, underscore)
    if re.match(r'^[a-zA-Z0-9_-]{20,}$', url_or_id):
        return url_or_id

    return url_or_id


def fetch_drive_image_bytes(drive_file_id):
    """
    Retrieves actual image bytes for a Google Drive file ID.
    Uses local cache, Google Drive API (if credentials configured), or HTTP endpoints securely on the backend.
    """
    if not drive_file_id:
        return None, None

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{drive_file_id}.jpg")

    # 1. Return from cache if present
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        with open(cache_path, 'rb') as f:
            return f.read(), 'image/jpeg'

    image_bytes = None
    content_type = 'image/jpeg'

    # 2. Try Google Drive API if credentials available
    creds_path = getattr(settings, 'GOOGLE_CREDENTIALS_PATH', os.path.join(settings.BASE_DIR, 'google_credentials.json'))
    if os.path.exists(creds_path):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            import io

            credentials = service_account.Credentials.from_service_account_file(
                creds_path, scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            service = build('drive', 'v3', credentials=credentials)
            request = service.files().get_media(fileId=drive_file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            image_bytes = fh.getvalue()
        except Exception as e:
            print(f"[Drive API Warning] Could not download via API: {e}")

    # 3. Fallback to HTTP download endpoints if API was not used or failed
    if not image_bytes:
        download_urls = [
            f"https://lh3.googleusercontent.com/d/{drive_file_id}",
            f"https://drive.google.com/uc?export=download&id={drive_file_id}",
            f"https://drive.google.com/thumbnail?id={drive_file_id}&sz=w1200",
        ]
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})
        for url in download_urls:
            try:
                res = session.get(url, timeout=10, allow_redirects=True)
                if res.status_code == 200 and len(res.content) > 100:
                    ct = res.headers.get('Content-Type', '')
                    if 'image' in ct or res.content.startswith(b'\xff\xd8') or res.content.startswith(b'\x89PNG'):
                        image_bytes = res.content
                        if 'image/png' in ct:
                            content_type = 'image/png'
                        break
            except Exception as e:
                continue

    # Save to cache if successful
    if image_bytes:
        try:
            with open(cache_path, 'wb') as f:
                f.write(image_bytes)
        except Exception as e:
            print(f"[Cache Warning] Failed to cache image: {e}")

    return image_bytes, content_type


def attach_drive_photo_to_student(student, drive_url_or_id, is_primary=True):
    """
    Downloads photo from Google Drive link/ID, attaches it to the Student model
    (both profile_pic and StudentPhoto), and precomputes/caches the face embedding.
    """
    from django.core.files.base import ContentFile
    import json
    from .models import StudentPhoto, FaceEmbedding

    drive_id = extract_google_drive_file_id(drive_url_or_id)
    if not drive_id:
        return False, "Invalid Google Drive link or ID"

    img_bytes, content_type = fetch_drive_image_bytes(drive_id)
    if not img_bytes:
        return False, f"Could not download photo from Drive ID {drive_id}. Ensure link is set to 'Anyone with the link can view'."

    ext = 'jpg' if 'jpeg' in (content_type or '') or 'jpg' in (content_type or '') else 'png'
    reg_clean = str(student.register_number or student.registration_id or student.id).strip()
    filename = f"{reg_clean}.{ext}"

    # Save to profile_pic
    try:
        student.profile_pic.save(filename, ContentFile(img_bytes), save=True)
    except Exception as e:
        print(f"[Warning] Failed to save profile_pic: {e}")

    # Create/update StudentPhoto
    photo, _ = StudentPhoto.objects.get_or_create(
        student=student,
        drive_file_id=drive_id,
        defaults={
            'source_url': str(drive_url_or_id),
            'is_primary': is_primary,
            'photo_order': 1 if is_primary else 2
        }
    )
    if is_primary:
        photo.is_primary = True
        photo.source_url = str(drive_url_or_id)
        photo.save()
        student.photos.exclude(id=photo.id).update(is_primary=False)

    # Compute and save face embedding
    try:
        import face_recognition
        import numpy as np
        import cv2

        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            encodings = face_recognition.face_encodings(rgb_img)
            if encodings:
                FaceEmbedding.objects.filter(student=student).delete()
                FaceEmbedding.objects.create(
                    student=student,
                    embedding=json.dumps(encodings[0].tolist()),
                    photo_reference=photo,
                    model_name='face_recognition_dlib'
                )
    except Exception as e:
        print(f"[Warning] Face embedding computation skipped: {e}")

    return True, "Photo attached and cached successfully"

