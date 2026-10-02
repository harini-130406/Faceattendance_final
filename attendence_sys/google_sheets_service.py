import csv
import io
import os
import re
import requests
from django.conf import settings
from .models import Student, StudentPhoto
from .google_drive_helper import extract_google_drive_file_id, fetch_drive_image_bytes, attach_drive_photo_to_student

DEFAULT_HEADER_PATTERNS = {
    'register_number': [
        r'reg.*num', r'reg.*no', r'reg.*id', r'registration', r'register',
        r'roll.*num', r'roll.*no', r'roll', r'enroll.*no', r'enrollment',
        r'adm.*no', r'admission', r'student.*id', r'student.*reg',
        r'^reg\b', r'^roll\b', r'^id$'
    ],
    'name': [r'student.*name', r'full.*name', r'^name$', r'candidate.*name', r'student'],
    'first_name': [r'first.*name', r'fname', r'given.*name'],
    'last_name': [r'last.*name', r'lname', r'surname', r'family.*name'],
    'department': [r'dept', r'department', r'branch', r'stream', r'course', r'programme'],
    'year': [r'year', r'yr', r'class', r'batch', r'semester', r'sem'],
    'section': [r'section', r'sec'],
    'email': [r'email', r'mail', r'e-mail'],
    'phone': [r'phone', r'mobile', r'contact', r'ph.*no', r'cell'],
    'photo_1': [
        r'photo.*1', r'image.*1', r'profile.*photo', r'upload.*photo',
        r'drive.*link', r'drive.*photo', r'image.*url', r'photo.*link',
        r'photo.*url', r'image', r'photo', r'picture', r'avatar'
    ],
    'photo_2': [r'photo.*2', r'image.*2'],
    'photo_3': [r'photo.*3', r'image.*3'],
}

def normalize_academic_year(val, fallback='3'):
    """
    Robustly converts year variations ('3 rd yr', '3rd Year', '3rd', 'III', '3.0', 'Year 3')
    into standard Django model choice strings: '1', '2', '3', or '4'.
    """
    if not val:
        return str(fallback) if fallback else '3'
    s = str(val).strip().lower()
    if s.endswith('.0'):
        s = s[:-2].strip()

    # Direct 4th year check
    if any(k in s for k in ['4th', 'four', 'yr 4', 'year 4', 'iv']):
        return '4'
    # Direct 3rd year check (handles '3 rd yr', '3rd yr', '3rd year', 'iii', 'third', 'yr 3', etc.)
    if any(k in s for k in ['3rd', '3 rd', '3_rd', 'third', 'yr 3', 'year 3', 'iii']):
        return '3'
    # Direct 2nd year check
    if any(k in s for k in ['2nd', '2 nd', 'second', 'yr 2', 'year 2', 'ii']):
        return '2'
    # Direct 1st year check
    if any(k in s for k in ['1st', '1 st', 'first', 'yr 1', 'year 1']):
        return '1'

    for digit in ['4', '3', '2', '1']:
        if digit in s:
            return digit

    if s == 'i':
        return '1'

    return str(fallback) if fallback else '3'

def extract_spreadsheet_id(sheet_url_or_id):
    if not sheet_url_or_id:
        return ""
    sheet_url_or_id = str(sheet_url_or_id).strip()
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', sheet_url_or_id)
    if match:
        return match.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{20,}$', sheet_url_or_id):
        return sheet_url_or_id
    return sheet_url_or_id


def detect_column_mapping(headers):
    mapping = {}
    cleaned_headers = [re.sub(r'[\s_.-]+', ' ', str(h).strip().lower()) for h in headers]

    for field, patterns in DEFAULT_HEADER_PATTERNS.items():
        for idx, h in enumerate(cleaned_headers):
            if any(re.search(pat, h) for pat in patterns):
                mapping[field] = idx
                break

    return mapping


def fetch_sheet_csv(sheet_url_or_id):
    sheet_id = extract_spreadsheet_id(sheet_url_or_id)
    if not sheet_id:
        raise ValueError("Invalid Google Sheet URL or Spreadsheet ID provided.")

    creds_path = getattr(settings, 'GOOGLE_CREDENTIALS_PATH', os.path.join(settings.BASE_DIR, 'google_credentials.json'))
    if os.path.exists(creds_path):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                creds_path, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
            service = build('sheets', '4', credentials=credentials)
            sheet_meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
            first_sheet_name = sheet_meta['sheets'][0]['properties']['title']
            result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=first_sheet_name).execute()
            rows = result.get('values', [])
            if rows:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerows(rows)
                return output.getvalue()
        except Exception as e:
            print(f"[Sheets API Warning] Could not fetch via API: {e}")

    csv_urls = [
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv",
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv",
    ]
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    for url in csv_urls:
        try:
            res = session.get(url, timeout=15)
            if res.status_code == 200 and len(res.text.strip()) > 0 and not res.text.strip().startswith('<!DOCTYPE'):
                return res.text
        except Exception as e:
            continue

    raise RuntimeError("Failed to fetch Google Sheet data. If this is a local Excel/CSV file, pass the file path directly. If it is a Google Sheet URL, ensure the sharing permission is set to 'Anyone with the link can view'.")


def sync_google_sheet(sheet_source, custom_mapping=None, download_photos=True, drive_folder_url=None, enrolled_by=None, enrolled_by_name=None, default_year=None, default_branch=None, default_section=None, new_only=False):
    """
    Synchronizes Django database with Student Registration data.
    sheet_source can be:
      - UploadedFile (Django request.FILES)
      - Google Sheet URL or Spreadsheet ID
      - Local .xlsx / .xls Excel file path
      - Local .csv file path
      - Raw CSV text string

    new_only: If True, existing students matching register_number are untouched and skipped.
    """
    stats = {
        'total_rows': 0,
        'created': 0,
        'updated': 0,
        'skipped': 0,
        'photos_processed': 0,
        'errors': 0,
        'logs': []
    }

    reader = []
    
    # Check if sheet_source is a Django UploadedFile or file-like object
    if hasattr(sheet_source, 'read'):
        filename = getattr(sheet_source, 'name', '').lower()
        if hasattr(sheet_source, 'seek'):
            sheet_source.seek(0)
        
        # Read header bytes to detect xlsx format
        header_bytes = sheet_source.read(4)
        if hasattr(sheet_source, 'seek'):
            sheet_source.seek(0)

        if filename.endswith(('.xlsx', '.xls')) or header_bytes == b'PK\x03\x04':
            import openpyxl
            wb = openpyxl.load_workbook(sheet_source, data_only=True)
            ws = wb.active
            for row in ws.rows:
                reader.append([str(cell.value).strip() if cell.value is not None else "" for cell in row])
        else:
            # Assume CSV
            content = sheet_source.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='ignore')
            reader = list(csv.reader(io.StringIO(content)))
    else:
        sheet_source_str = str(sheet_source).strip()

        # Case 1: Local Excel file (.xlsx / .xls)
        if os.path.exists(sheet_source_str) and (sheet_source_str.lower().endswith('.xlsx') or sheet_source_str.lower().endswith('.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(sheet_source_str, data_only=True)
            ws = wb.active
            for row in ws.rows:
                reader.append([str(cell.value).strip() if cell.value is not None else "" for cell in row])

        # Case 2: Local CSV file
        elif os.path.exists(sheet_source_str):
            with open(sheet_source_str, 'r', encoding='utf-8', errors='ignore') as f:
                reader = list(csv.reader(f))

        # Case 3: Google Sheet URL or ID
        elif "http" in sheet_source_str or (len(sheet_source_str) >= 20 and not '\n' in sheet_source_str):
            csv_data = fetch_sheet_csv(sheet_source_str)
            reader = list(csv.reader(io.StringIO(csv_data)))

        # Case 4: Raw CSV text string
        else:
            reader = list(csv.reader(io.StringIO(sheet_source_str)))

    if not reader or len(reader) < 2:
        raise ValueError("The provided file or sheet contains no data rows.")

    headers = reader[0]
    data_rows = reader[1:]
    stats['total_rows'] = len(data_rows)

    col_map = custom_mapping or detect_column_mapping(headers)

    reg_idx = col_map.get('register_number')
    if reg_idx is None:
        raise ValueError(f"Could not identify 'Registration ID / Register Number' column from headers: {headers}. Supported headers include: Registration ID, Register Number, Reg No, Roll No, Student ID.")

    name_idx = col_map.get('name')
    fname_idx = col_map.get('first_name')
    lname_idx = col_map.get('last_name')
    dept_idx = col_map.get('department')
    year_idx = col_map.get('year')
    sec_idx = col_map.get('section')
    email_idx = col_map.get('email')
    phone_idx = col_map.get('phone')
    photo1_idx = col_map.get('photo_1')
    photo2_idx = col_map.get('photo_2')
    photo3_idx = col_map.get('photo_3')

    def get_val(row, idx):
        if idx is not None and 0 <= idx < len(row):
            val = str(row[idx]).strip()
            if val == 'None':
                return ""
            if val.endswith('.0') and val[:-2].replace('-', '').replace('+', '').isdigit():
                return val[:-2]
            return val
        return ""

    for i, row in enumerate(data_rows, start=1):
        if not any(row):
            continue

        try:
            reg_num = get_val(row, reg_idx)
            if not reg_num:
                stats['errors'] += 1
                stats['logs'].append(f"Row {i}: Skipped due to missing Register Number.")
                continue

            existing_student = Student.objects.filter(register_number=reg_num).first()
            if existing_student and new_only:
                stats['skipped'] += 1
                stats['logs'].append(f"[Skipped] Student {reg_num} already exists (Add New Only enabled).")
                continue

            name = get_val(row, name_idx)
            fname = get_val(row, fname_idx)
            lname = get_val(row, lname_idx)

            raw_dept = get_val(row, dept_idx)
            department = (raw_dept or default_branch or 'CSE').upper()
            raw_year = get_val(row, year_idx)
            year = normalize_academic_year(raw_year, fallback=default_year or '3')
            raw_sec = get_val(row, sec_idx)
            section = (raw_sec or default_section or 'A').upper()
            email = get_val(row, email_idx)
            phone = get_val(row, phone_idx)

            if existing_student:
                student = existing_student
                created = False
                stats['updated'] += 1
                stats['logs'].append(f"[OK] Student {reg_num} updated")
                if not student.enrolled_by and enrolled_by:
                    student.enrolled_by = enrolled_by
                    student.enrolled_by_name = enrolled_by_name or student.enrolled_by_name
            else:
                student = Student(register_number=reg_num)
                created = True
                stats['created'] += 1
                stats['logs'].append(f"[OK] Student {reg_num} created")
                if enrolled_by:
                    student.enrolled_by = enrolled_by
                student.enrolled_by_name = enrolled_by_name or 'Institutional Registry'

            student.registration_id = reg_num
            if name:
                student.name = name
            if fname:
                student.firstname = fname
            if lname:
                student.lastname = lname
            if not student.name and (student.firstname or student.lastname):
                student.name = f"{student.firstname or ''} {student.lastname or ''}".strip()

            if department:
                student.department = department
                student.branch = department
            if year:
                student.year = year
            if section:
                student.section = section
            if email:
                student.email = email
            if phone:
                student.phone = phone
            student.save()

            # Process Photos
            photo_urls = [
                (get_val(row, photo1_idx), True, 1),
                (get_val(row, photo2_idx), False, 2),
                (get_val(row, photo3_idx), False, 3),
            ]

            for p_url, is_primary, order in photo_urls:
                if not p_url:
                    continue
                drive_id = extract_google_drive_file_id(p_url)
                if not drive_id:
                    continue

                stats['photos_processed'] += 1
                if download_photos:
                    try:
                        success, msg = attach_drive_photo_to_student(student, p_url, is_primary=is_primary)
                        if success:
                            stats['logs'].append(f"[OK] Downloaded and attached photo for {reg_num}")
                        else:
                            stats['logs'].append(f"[Warning] Photo for {reg_num}: {msg}")
                    except Exception as photo_err:
                        stats['logs'].append(f"[Warning] Photo error for {reg_num}: {photo_err}")
                else:
                    StudentPhoto.objects.get_or_create(
                        student=student,
                        drive_file_id=drive_id,
                        defaults={'source_url': p_url, 'is_primary': is_primary, 'photo_order': order}
                    )
        except Exception as row_exc:
            stats['errors'] += 1
            stats['logs'].append(f"Row {i} error: {row_exc}")
            continue

    return stats


def generate_excel_template():
    """
    Generates a template Excel file for bulk student import.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Student Roster"

    headers = [
        "Registration ID",
        "First Name",
        "Last Name",
        "Branch",
        "Year",
        "Section",
        "Email",
        "Phone",
        "Google Drive Photo Link"
    ]

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")

    ws.append(headers)

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center

    # Sample rows
    samples = [
        ["715524104001", "Aarav", "Sharma", "CSE", "3", "A", "aarav@example.com", "9876543210", "https://drive.google.com/file/d/1SAMPLE_DRIVE_ID_1/view"],
        ["715524104002", "Priya", "Nair", "CSE", "3", "A", "priya@example.com", "9876543211", "https://drive.google.com/file/d/1SAMPLE_DRIVE_ID_2/view"],
        ["715524104003", "Rahul", "Verma", "ECE", "2", "B", "rahul@example.com", "9876543212", "https://drive.google.com/file/d/1SAMPLE_DRIVE_ID_3/view"],
    ]

    for row_data in samples:
        ws.append(row_data)

    # Set column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

