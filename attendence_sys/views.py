from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.conf import settings
import os
import io
import json
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Count, Q

from .forms import CreateStudentForm, FacultyForm
from .models import Student, StudentPhoto, Attendence, AttendanceSession, Faculty, FaceEmbedding
from .filters import AttendenceFilter
from .google_drive_helper import fetch_drive_image_bytes, attach_drive_photo_to_student
from .google_sheets_service import sync_google_sheet, generate_excel_template
from .detector import process_classroom_image, get_student_face_encoding
import face_recognition
import cv2
import numpy as np


def normalize_date_input(val):
    """
    Parses any date format (YYYY-MM-DD, Month DD, YYYY, DD/MM/YYYY, etc.)
    and returns (datetime.date_obj, 'YYYY-MM-DD' ISO string).
    """
    if not val:
        today = date.today()
        return today, today.strftime('%Y-%m-%d')
    if isinstance(val, date):
        return val, val.strftime('%Y-%m-%d')

    val_str = str(val).strip()
    formats = [
        '%Y-%m-%d',
        '%B %d, %Y',   # July 2, 2020
        '%b %d, %Y',   # Jul 2, 2020
        '%B %d %Y',
        '%b %d %Y',
        '%d-%m-%Y',
        '%d/%m/%Y',
        '%m/%d/%Y',
        '%Y/%m/%d',
        '%d %B %Y',
        '%d %b %Y',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(val_str, fmt).date()
            return dt, dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue

    today = date.today()
    return today, today.strftime('%Y-%m-%d')



@login_required(login_url='login')
def home(request):
    """
    Modern attendance dashboard with statistics, quick actions, and recent activity.
    """
    total_students = Student.objects.count()
    today_date = date.today()

    today_attendances = Attendence.objects.filter(date=today_date)
    today_sessions = AttendanceSession.objects.filter(date=today_date).order_by('-created_at')

    today_present = today_attendances.filter(status__iexact='Present').count()
    today_absent = today_attendances.filter(status__iexact='Absent').count()
    today_total = today_present + today_absent
    attendance_rate = round((today_present / today_total * 100), 1) if today_total > 0 else 0.0

    recent_sessions = AttendanceSession.objects.all().order_by('-created_at')[:6]
    recent_students = Student.objects.all().order_by('-id')[:8]

    # Distinct slot summaries from Attendence
    distinct_slots = Attendence.objects.filter(date=today_date).values(
        'branch', 'year', 'section', 'period'
    ).distinct()

    context = {
        'total_students': total_students,
        'today_sessions_count': distinct_slots.count() if distinct_slots.exists() else today_sessions.count(),
        'today_present': today_present,
        'today_absent': today_absent,
        'attendance_rate': attendance_rate,
        'recent_sessions': recent_sessions,
        'recent_students': recent_students,
        'today_slots': distinct_slots,
        'today_date': today_date,
    }
    return render(request, 'attendence_sys/home.html', context)


@login_required(login_url='login')
def takeAttendancePage(request):
    """
    Dedicated view for taking attendance.
    Supports classroom group photo upload with AI face detection,
    confidence scoring, and on-the-spot manual editing before saving.
    """
    faculty = getattr(request.user, 'faculty', None)
    today_str = str(date.today())

    # Pre-fill options
    branches = ['CSE', 'IT', 'ECE', 'EEE', 'MECH', 'CHEM', 'CIVIL']
    years = ['1', '2', '3', '4']
    sections = ['A', 'B', 'C']
    periods = ['1', '2', '3', '4', '5', '6', '7']

    context = {
        'branches': branches,
        'years': years,
        'sections': sections,
        'periods': periods,
        'today_str': today_str,
        'step': 'select', # 'select' or 'review'
    }

    if request.method == 'POST':
        action = request.POST.get('action', 'detect')

        branch = request.POST.get('branch', 'CSE').strip()
        year = request.POST.get('year', '1').strip()
        section = request.POST.get('section', 'A').strip().upper()
        period = request.POST.get('period', '1').strip()
        raw_date = request.POST.get('date', today_str).strip() or today_str
        date_obj, date_iso = normalize_date_input(raw_date)

        context.update({
            'selected_branch': branch,
            'selected_year': year,
            'selected_section': section,
            'selected_period': period,
            'selected_date': date_iso,
        })

        # Check existing attendance in DB
        existing_records = Attendence.objects.filter(
            date=date_obj,
            branch=branch,
            year=year,
            section=section,
            period=period
        )
        context['existing_count'] = existing_records.count()
        context['existing_present'] = existing_records.filter(status__iexact='Present').count()

        if action == 'detect':
            classroom_file = request.FILES.get('classroom_image')

            # Fetch enrolled students
            enrolled = Student.objects.filter(
                branch__iexact=branch,
                year=str(year),
                section__iexact=section
            ).order_by('register_number', 'registration_id')

            if not enrolled.exists():
                enrolled = Student.objects.filter(
                    department__iexact=branch,
                    year=str(year),
                    section__iexact=section
                ).order_by('register_number', 'registration_id')

            if not enrolled.exists():
                messages.warning(
                    request,
                    f"No students found enrolled in Branch: {branch}, Year: {year}, Section: {section}. Please add students first."
                )
                return render(request, 'attendence_sys/take_attendance.html', context)

            if not classroom_file:
                # If no image uploaded, provide manual attendance roster immediately
                roster = []
                for s in enrolled:
                    # check if existing record
                    rec = existing_records.filter(Student_ID=s.register_number or s.registration_id).first()
                    status = rec.status if rec else 'Absent'
                    roster.append({
                        'student': s,
                        'status': status,
                        'confidence': rec.confidence if rec else None,
                        'has_photo': bool(s.profile_pic.name != '' or s.photos.exists()),
                        'photo_url': s.get_primary_photo_url(),
                    })
                context.update({
                    'step': 'review',
                    'roster': roster,
                    'total_enrolled': len(roster),
                    'present_count': sum(1 for r in roster if r['status'] == 'Present'),
                    'absent_count': sum(1 for r in roster if r['status'] != 'Present'),
                    'total_detected_faces': 0,
                    'annotated_image': None,
                })
                messages.info(request, "Manual attendance mode loaded. Toggle student statuses and click Save.")
                return render(request, 'attendence_sys/take_attendance.html', context)

            # Process classroom image with AI
            try:
                detection_result = process_classroom_image(
                    classroom_file,
                    branch=branch,
                    year=year,
                    section=section,
                    tolerance=0.55
                )

                # Save session image if uploaded
                session, _ = AttendanceSession.objects.get_or_create(
                    date=date_obj,
                    department=branch,
                    year=year,
                    section=section,
                    defaults={'teacher': str(faculty) if faculty else 'Faculty'}
                )
                try:
                    classroom_file.seek(0)
                    session.classroom_image.save(f"classroom_{branch}_{year}_{section}_{period}.jpg", classroom_file, save=True)
                except Exception:
                    pass

                context.update({
                    'step': 'review',
                    'roster': detection_result['roster'],
                    'total_enrolled': detection_result['total_enrolled'],
                    'total_detected_faces': detection_result['total_detected_faces'],
                    'present_count': detection_result['present_count'],
                    'absent_count': detection_result['absent_count'],
                    'attendance_rate': detection_result['attendance_rate'],
                    'annotated_image': detection_result['annotated_image'],
                })
                messages.success(
                    request,
                    f"Classroom image analyzed: {detection_result['total_detected_faces']} faces detected. {detection_result['present_count']} marked Present, {detection_result['absent_count']} Absent. You can review/edit below before saving."
                )
                return render(request, 'attendence_sys/take_attendance.html', context)

            except Exception as e:
                messages.error(request, f"Error analyzing classroom image: {e}")
                return render(request, 'attendence_sys/take_attendance.html', context)

        elif action == 'save':
            # Save or update attendance records in database
            student_ids = request.POST.getlist('student_id')
            saved_present = 0
            saved_absent = 0

            for s_id in student_ids:
                status = request.POST.get(f"status_{s_id}", "Absent")
                confidence_str = request.POST.get(f"confidence_{s_id}", "0.0")
                try:
                    confidence = float(confidence_str) if confidence_str else 0.0
                except ValueError:
                    confidence = 0.0

                student_obj = Student.objects.filter(
                    Q(register_number=s_id) | Q(registration_id=s_id)
                ).first()

                existing_recs = Attendence.objects.filter(
                    date=date_obj,
                    branch=branch,
                    year=year,
                    section=section,
                    period=period,
                    Student_ID=s_id
                )
                if existing_recs.exists():
                    rec = existing_recs.first()
                    rec.Faculty_Name = str(faculty) if faculty else 'Faculty'
                    rec.student_ref = student_obj
                    rec.status = status
                    rec.confidence = confidence if status == 'Present' else 0.0
                    rec.save()
                    if existing_recs.count() > 1:
                        existing_recs.exclude(id=rec.id).delete()
                else:
                    Attendence.objects.create(
                        date=date_obj,
                        branch=branch,
                        year=year,
                        section=section,
                        period=period,
                        Student_ID=s_id,
                        Faculty_Name=str(faculty) if faculty else 'Faculty',
                        student_ref=student_obj,
                        status=status,
                        confidence=confidence if status == 'Present' else 0.0,
                    )

                if status == 'Present':
                    saved_present += 1
                else:
                    saved_absent += 1

            total_saved = saved_present + saved_absent
            messages.success(
                request,
                f"Attendance saved successfully for {branch} Year {year}-{section} Period {period}: {saved_present} Present, {saved_absent} Absent (Total {total_saved})."
            )
            return redirect(f"/searchattendence/?branch={branch}&year={year}&section={section}&period={period}&date={date_iso}")

    return render(request, 'attendence_sys/take_attendance.html', context)


@login_required(login_url='login')
def editAttendance(request):
    """
    Dedicated view to edit previously taken or current attendance records.
    Allows manual toggling of status between Present and Absent with instant save.
    """
    faculty = getattr(request.user, 'faculty', None)
    today_str = str(date.today())

    branches = ['CSE', 'IT', 'ECE', 'EEE', 'MECH', 'CHEM', 'CIVIL']
    years = ['1', '2', '3', '4']
    sections = ['A', 'B', 'C']
    periods = ['1', '2', '3', '4', '5', '6', '7']

    branch = request.GET.get('branch') or request.POST.get('branch', 'CSE')
    year = request.GET.get('year') or request.POST.get('year', '1')
    section = request.GET.get('section') or request.POST.get('section', 'A')
    period = request.GET.get('period') or request.POST.get('period', '1')
    raw_date = request.GET.get('date') or request.POST.get('date') or today_str
    date_obj, date_iso = normalize_date_input(raw_date)

    context = {
        'branches': branches,
        'years': years,
        'sections': sections,
        'periods': periods,
        'selected_branch': branch,
        'selected_year': year,
        'selected_section': section,
        'selected_period': period,
        'selected_date': date_iso,
    }

    if request.method == 'POST' and request.POST.get('save_edits') == '1':
        student_ids = request.POST.getlist('student_id')
        updated_count = 0
        p_count = 0
        a_count = 0

        for s_id in student_ids:
            new_status = request.POST.get(f"status_{s_id}", "Absent")
            student_obj = Student.objects.filter(
                Q(register_number=s_id) | Q(registration_id=s_id)
            ).first()

            existing_recs = Attendence.objects.filter(
                date=date_obj,
                branch=branch,
                year=year,
                section=section,
                period=period,
                Student_ID=s_id
            )
            if existing_recs.exists():
                rec = existing_recs.first()
                rec.Faculty_Name = str(faculty) if faculty else 'Faculty'
                rec.student_ref = student_obj
                rec.status = new_status
                rec.save()
                if existing_recs.count() > 1:
                    existing_recs.exclude(id=rec.id).delete()
            else:
                Attendence.objects.create(
                    date=date_obj,
                    branch=branch,
                    year=year,
                    section=section,
                    period=period,
                    Student_ID=s_id,
                    Faculty_Name=str(faculty) if faculty else 'Faculty',
                    student_ref=student_obj,
                    status=new_status,
                )
            updated_count += 1
            if new_status == 'Present':
                p_count += 1
            else:
                a_count += 1

        messages.success(request, f"Attendance updated: {p_count} Present, {a_count} Absent ({updated_count} students updated).")
        return redirect(f"/searchattendence/?branch={branch}&year={year}&section={section}&period={period}&date={date_iso}")

    # Load students and their recorded statuses
    enrolled = Student.objects.filter(
        branch__iexact=branch,
        year=str(year),
        section__iexact=section
    ).order_by('register_number', 'registration_id')

    if not enrolled.exists():
        enrolled = Student.objects.filter(
            department__iexact=branch,
            year=str(year),
            section__iexact=section
        ).order_by('register_number', 'registration_id')

    records_qs = Attendence.objects.filter(
        date=date_obj, branch=branch, year=year, section=section, period=period
    )
    records_map = {att.Student_ID: att for att in records_qs}

    roster = []
    seen_ids = set()

    for s in enrolled:
        reg_id = s.register_number or s.registration_id
        if not reg_id:
            continue
        seen_ids.add(str(reg_id).strip())
        att_rec = records_map.get(reg_id)
        current_status = att_rec.status if att_rec else 'Absent'
        roster.append({
            'student': s,
            'status': current_status,
            'photo_url': s.get_primary_photo_url(),
            'confidence': att_rec.confidence if att_rec else None,
        })

    # Include any attendance records that were saved for this slot even if student wasn't in filtered enrolled
    for att in records_qs:
        if att.Student_ID and str(att.Student_ID).strip() not in seen_ids:
            seen_ids.add(str(att.Student_ID).strip())
            st_obj = att.student_ref or Student.objects.filter(
                Q(register_number=att.Student_ID) | Q(registration_id=att.Student_ID)
            ).first()
            roster.append({
                'student': st_obj or Student(registration_id=att.Student_ID, name=att.Student_ID),
                'status': att.status or 'Absent',
                'photo_url': st_obj.get_primary_photo_url() if st_obj else '/static/images/default_avatar.png',
                'confidence': att.confidence,
            })

    context.update({
        'roster': roster,
        'total_students': len(roster),
        'present_count': sum(1 for r in roster if r['status'] == 'Present'),
        'absent_count': sum(1 for r in roster if r['status'] != 'Present'),
        'has_records': bool(records_map),
    })

    return render(request, 'attendence_sys/attendance_edit.html', context)


@login_required(login_url='login')
def addStudentPage(request):
    """
    Dedicated student registration page supporting:
    1. Single student add with photo upload OR Google Drive image link.
    2. Bulk import via Excel (.xlsx, .xls) / CSV file + Google Drive image links.
    """
    context = {'active_tab': 'single'}

    if request.method == 'POST':
        form_type = request.POST.get('form_type', 'single')

        if form_type == 'single':
            reg_id = request.POST.get('registration_id', '').strip()
            fname = request.POST.get('firstname', '').strip()
            lname = request.POST.get('lastname', '').strip()
            branch = request.POST.get('branch', 'CSE').strip()
            year = request.POST.get('year', '1').strip()
            section = request.POST.get('section', 'A').strip().upper()
            email = request.POST.get('email', '').strip()
            phone = request.POST.get('phone', '').strip()
            drive_link = request.POST.get('drive_link', '').strip()
            profile_pic_file = request.FILES.get('profile_pic')

            if not reg_id:
                messages.error(request, "Registration ID / Register Number is required.")
                return render(request, 'attendence_sys/add_student.html', context)

            # Check if exists
            student, created = Student.objects.get_or_create(
                register_number=reg_id,
                defaults={
                    'registration_id': reg_id,
                    'firstname': fname,
                    'lastname': lname,
                    'name': f"{fname} {lname}".strip(),
                    'branch': branch,
                    'department': branch,
                    'year': year,
                    'section': section,
                    'email': email,
                    'phone': phone,
                }
            )

            if not created:
                student.firstname = fname or student.firstname
                student.lastname = lname or student.lastname
                student.name = f"{student.firstname} {student.lastname}".strip()
                student.branch = branch
                student.department = branch
                student.year = year
                student.section = section
                if email: student.email = email
                if phone: student.phone = phone
                student.save()

            # Handle photo: Priority 1: Uploaded file, Priority 2: Google Drive link
            if profile_pic_file:
                student.profile_pic = profile_pic_file
                student.save()
                # Compute face embedding
                try:
                    get_student_face_encoding(student)
                except Exception:
                    pass
                messages.success(request, f"Student {student.name or reg_id} registered with uploaded photo.")

            elif drive_link:
                ok, msg = attach_drive_photo_to_student(student, drive_link, is_primary=True)
                if ok:
                    messages.success(request, f"Student {student.name or reg_id} registered and Google Drive photo downloaded successfully.")
                else:
                    messages.warning(request, f"Student registered, but photo could not be fetched from Drive link: {msg}")
            else:
                messages.success(request, f"Student {student.name or reg_id} registered successfully (no photo provided).")

            return redirect('add_student')

        elif form_type == 'bulk':
            context['active_tab'] = 'bulk'
            excel_file = request.FILES.get('excel_file')
            sheet_url = request.POST.get('sheet_url', '').strip()
            drive_folder = request.POST.get('drive_folder_url', '').strip()

            source = excel_file if excel_file else sheet_url
            if not source:
                messages.error(request, "Please choose an Excel / CSV file or enter a Google Sheet URL.")
                return render(request, 'attendence_sys/add_student.html', context)

            try:
                stats = sync_google_sheet(
                    source,
                    download_photos=True,
                    drive_folder_url=drive_folder if drive_folder else None
                )
                context['bulk_stats'] = stats
                messages.success(
                    request,
                    f"Bulk import completed! Rows: {stats['total_rows']}, Created: {stats['created']}, Updated: {stats['updated']}, Photos processed: {stats['photos_processed']}, Errors: {stats['errors']}."
                )
            except Exception as e:
                messages.error(request, f"Bulk import failed: {e}")

    return render(request, 'attendence_sys/add_student.html', context)


@login_required(login_url='login')
def downloadExcelTemplateView(request):
    """
    Downloads pre-formatted Excel template for student bulk import.
    """
    try:
        template_io = generate_excel_template()
        response = HttpResponse(
            template_io.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="student_roster_template.xlsx"'
        return response
    except Exception as e:
        messages.error(request, f"Failed to generate template: {e}")
        return redirect('add_student')


@login_required(login_url='login')
def studentDirectory(request):
    """
    Full student directory with search, filter, and avatar gallery.
    """
    branch = request.GET.get('branch', '')
    year = request.GET.get('year', '')
    section = request.GET.get('section', '')
    search = request.GET.get('q', '').strip()

    students = Student.objects.all().order_by('branch', 'year', 'section', 'register_number')

    if branch:
        students = students.filter(Q(branch__iexact=branch) | Q(department__iexact=branch))
    if year:
        students = students.filter(year=year)
    if section:
        students = students.filter(section__iexact=section)
    if search:
        students = students.filter(
            Q(register_number__icontains=search) |
            Q(registration_id__icontains=search) |
            Q(name__icontains=search) |
            Q(firstname__icontains=search) |
            Q(lastname__icontains=search)
        )

    context = {
        'students': students,
        'branches': ['CSE', 'IT', 'ECE', 'EEE', 'MECH', 'CHEM', 'CIVIL'],
        'years': ['1', '2', '3', '4'],
        'sections': ['A', 'B', 'C'],
        'selected_branch': branch,
        'selected_year': year,
        'selected_section': section,
        'search_query': search,
        'total_count': students.count(),
    }
    return render(request, 'attendence_sys/students_list.html', context)


@login_required(login_url='login')
def searchAttendence(request):
    """
    Modern attendance history and search view with edit links for every recorded slot.
    """
    get_params = request.GET.copy()
    if 'date' in get_params and get_params['date']:
        _, date_iso = normalize_date_input(get_params['date'])
        get_params['date'] = date_iso

    attendences = Attendence.objects.all().order_by('-date', '-period', 'branch', 'section')
    myFilter = AttendenceFilter(get_params, queryset=attendences)
    filtered_qs = myFilter.qs

    # Aggregated stats
    p_count = filtered_qs.filter(status__iexact='Present').count()
    a_count = filtered_qs.filter(status__iexact='Absent').count()
    tot = p_count + a_count
    pct = round(p_count / tot * 100, 1) if tot > 0 else 0.0

    context = {
        'myFilter': myFilter,
        'attendences': filtered_qs,
        'ta': False,
        'present_count': p_count,
        'absent_count': a_count,
        'total_count': tot,
        'attendance_percentage': pct,
    }
    return render(request, 'attendence_sys/attendence.html', context)


# Maintain backward compatibility with old takeAttendence endpoint
@login_required(login_url='login')
def takeAttendence(request):
    return redirect('take_attendance')


def loginPage(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=username, password=password)
        if user is None and username:
            try:
                from django.contrib.auth.models import User
                matched_user = User.objects.get(username__iexact=username)
                user = authenticate(request, username=matched_user.username, password=password)
            except Exception:
                user = None

        if user is not None:
            login(request, user)
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password')

    context = {}
    return render(request, 'attendence_sys/login.html', context)


@login_required(login_url='login')
def logoutUser(request):
    logout(request)
    return redirect('login')


@login_required(login_url='login')
def editStudent(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        reg_id = request.POST.get('registration_id', '').strip()
        fname = request.POST.get('firstname', '').strip()
        lname = request.POST.get('lastname', '').strip()
        branch = request.POST.get('branch', '').strip()
        year = request.POST.get('year', '').strip()
        section = request.POST.get('section', '').strip().upper()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        drive_link = request.POST.get('drive_link', '').strip()
        profile_pic_file = request.FILES.get('profile_pic')

        if reg_id:
            existing = Student.objects.filter(Q(register_number=reg_id) | Q(registration_id=reg_id)).exclude(id=student.id).first()
            if existing:
                messages.error(request, f"Another student already has Registration ID '{reg_id}'.")
                return render(request, 'attendence_sys/student_update.html', {'student': student, 'prev_reg_id': student.register_number})
            student.register_number = reg_id
            student.registration_id = reg_id

        student.firstname = fname
        student.lastname = lname
        student.name = f"{fname} {lname}".strip() or student.name
        if branch:
            student.branch = branch
            student.department = branch
        if year:
            student.year = year
        if section:
            student.section = section
        student.email = email
        student.phone = phone

        if profile_pic_file:
            student.profile_pic = profile_pic_file
            student.save()
            try:
                get_student_face_encoding(student)
            except Exception:
                pass
            messages.success(request, f"Student {student.name or student.register_number} updated with new uploaded photo.")
        elif drive_link:
            student.save()
            ok, msg = attach_drive_photo_to_student(student, drive_link, is_primary=True)
            if ok:
                messages.success(request, f"Student {student.name or student.register_number} updated and new Google Drive photo downloaded.")
            else:
                messages.warning(request, f"Student details updated, but Drive photo failed: {msg}")
        else:
            student.save()
            messages.success(request, f"Student {student.name or student.register_number} updated successfully.")

        return redirect('student_directory')

    context = {
        'student': student,
        'prev_reg_id': student.register_number or student.registration_id or '',
    }
    return render(request, 'attendence_sys/student_update.html', context)


@login_required(login_url='login')
def deleteStudent(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    name = str(student.name or student.register_number or student.registration_id or f"Student #{student.id}")

    student.embeddings.all().delete()
    student.photos.all().delete()
    student.delete()

    messages.success(request, f"Student '{name}' has been successfully deleted from the database.")
    return redirect('student_directory')


@login_required(login_url='login')
def bulkDeleteStudents(request):
    if request.method == 'POST':
        ids = request.POST.getlist('selected_students')
        if not ids:
            messages.warning(request, "No students were selected for deletion.")
            return redirect('student_directory')

        students = Student.objects.filter(id__in=ids)
        count = students.count()
        for s in students:
            s.embeddings.all().delete()
            s.photos.all().delete()
        students.delete()

        messages.success(request, f"Successfully deleted {count} selected student(s) from the database.")
    return redirect('student_directory')


@login_required(login_url='login')
def updateStudentRedirect(request):
    if request.method == 'POST':
        reg_id = request.POST.get('reg_id')
        student = Student.objects.filter(Q(registration_id=reg_id) | Q(register_number=reg_id)).first()
        if student:
            return redirect('edit_student', student_id=student.id)
    messages.error(request, 'Student Not Found')
    return redirect('student_directory')


@login_required(login_url='login')
def updateStudent(request):
    return redirect('student_directory')



@login_required(login_url='login')
def facultyProfile(request):
    faculty = getattr(request.user, 'faculty', None)
    if not faculty:
        faculty = Faculty.objects.create(user=request.user, firstname=request.user.username)
    form = FacultyForm(instance=faculty)

    if request.method == 'POST':
        form = FacultyForm(data=request.POST, files=request.FILES, instance=faculty)
        if form.is_valid():
            form.save()
            messages.success(request, 'Faculty details updated successfully.')
            return redirect('account')

    context = {'form': form}
    return render(request, 'attendence_sys/facultyForm.html', context)


def studentPrimaryPhoto(request, student_id):
    try:
        student = Student.objects.get(id=student_id)
    except Student.DoesNotExist:
        return HttpResponse(status=404)

    primary_photo = student.get_primary_photo()
    if primary_photo and primary_photo.drive_file_id:
        img_bytes, content_type = fetch_drive_image_bytes(primary_photo.drive_file_id)
        if img_bytes:
            return HttpResponse(img_bytes, content_type=content_type)

    if student.profile_pic and os.path.exists(student.profile_pic.path):
        with open(student.profile_pic.path, 'rb') as f:
            return HttpResponse(f.read(), content_type='image/jpeg')

    placeholder_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'default_avatar.png')
    if os.path.exists(placeholder_path):
        with open(placeholder_path, 'rb') as f:
            return HttpResponse(f.read(), content_type='image/png')

    return HttpResponse(status=404)


def studentPhotoById(request, photo_id):
    try:
        photo = StudentPhoto.objects.get(id=photo_id)
    except StudentPhoto.DoesNotExist:
        return HttpResponse(status=404)

    if photo.drive_file_id:
        img_bytes, content_type = fetch_drive_image_bytes(photo.drive_file_id)
        if img_bytes:
            return HttpResponse(img_bytes, content_type=content_type)

    return HttpResponse(status=404)