import os
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_bcrypt import Bcrypt
from models import db, Student, Lecturer, Attendance, ClassSession, Faculty, Department, Course, LecturerCourse, Admin, StudentCourse, session_departments
from datetime import datetime, timedelta
import io, csv, string
from sms_service import send_attendance_alert, send_absence_warning
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

# ---- Temporary development secrets (REMOVE FOR PRODUCTION) ----
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-later')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
# ----------------------------------------------------------------

bcrypt = Bcrypt(app)

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+mysqlconnector://root:{DB_PASSWORD}@localhost/smart_attendance'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

# ---------- Helper Functions ----------
def haversine(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def generate_numeric_token(length=6):
    return ''.join(secrets.choice(string.digits) for _ in range(length))

def validate_pattern_server(points, canvas_width, canvas_height, pattern_name):
    if not points or len(points) < 10:
        return False
    xs = [p['x'] for p in points]
    ys = [p['y'] for p in points]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width == 0 or height == 0:
        return False
    aspect_ratio = width / height
    first = points[0]
    last = points[-1]
    closure_dist = ((last['x'] - first['x'])**2 + (last['y'] - first['y'])**2) ** 0.5
    is_closed = closure_dist < 80
    pattern = pattern_name.lower()
    if pattern == 'circle':
        return is_closed and 0.4 < aspect_ratio < 2.5 and len(points) > 25
    elif pattern == 'triangle':
        return is_closed and len(points) > 12
    elif pattern == 'square':
        return is_closed and 0.5 < aspect_ratio < 2.0
    elif pattern == 'zigzag':
        return not is_closed and width > 80 and len(points) > 15
    elif pattern == 'star':
        return is_closed and len(points) > 35
    else:
        return False

# ---------- Public API Endpoints ----------
@app.route('/api/faculties')
def api_faculties():
    return jsonify([{'id': f.id, 'name': f.name} for f in Faculty.query.order_by(Faculty.name).all()])

@app.route('/api/departments/<int:faculty_id>')
def api_departments(faculty_id):
    depts = Department.query.filter_by(faculty_id=faculty_id).order_by(Department.name).all()
    return jsonify([{'id': d.id, 'name': d.name} for d in depts])

@app.route('/api/levels/<int:department_id>')
def api_levels(department_id):
    levels = db.session.query(Course.level).filter_by(department_id=department_id).distinct().order_by(Course.level).all()
    return jsonify([level[0] for level in levels])

@app.route('/api/all_courses')
def api_all_courses():
    faculty_id = request.args.get('faculty_id', type=int)
    department_id = request.args.get('department_id', type=int)
    level = request.args.get('level', type=int)
    query = Course.query
    if department_id:
        query = query.filter_by(department_id=department_id)
    elif faculty_id:
        query = query.join(Department).filter(Department.faculty_id == faculty_id)
    if level:
        query = query.filter_by(level=level)
    courses = query.order_by(Course.code).all()
    return jsonify([{
        'id': c.id, 'code': c.code, 'title': c.title,
        'level': c.level, 'semester': c.semester,
        'department': c.department.name,
        'faculty': c.department.faculty.name
    } for c in courses])

@app.route('/api/my_courses')
def api_my_courses():
    if 'lecturer_id' not in session:
        return jsonify([])
    lcs = LecturerCourse.query.filter_by(lecturer_id=session['lecturer_id']).all()
    return jsonify([{'id': lc.course.id, 'code': lc.course.code, 'title': lc.course.title, 'level': lc.course.level} for lc in lcs])

@app.route('/api/course_departments/<int:course_id>')
def api_course_departments(course_id):
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify([])
    dept_ids = db.session.query(Student.department_id).join(StudentCourse).filter(
        StudentCourse.course_id == course_id,
        Student.id == StudentCourse.student_id,
        Student.department_id.isnot(None)
    ).distinct().all()
    dept_list = []
    for (did,) in dept_ids:
        d = db.session.get(Department, did)
        if d:
            dept_list.append({'id': d.id, 'name': d.name})
    return jsonify(dept_list)

# ---------- Student Routes ----------
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        matric_no = request.form['matric_no']
        password = request.form['password']
        device_hash = request.form.get('device_hash', '')
        student = Student.query.filter_by(matric_no=matric_no).first()
        if not student or not bcrypt.check_password_hash(student.password, password):
            return render_template('login.html', error='Invalid matric number or password')
        if student.device_hash and student.device_hash != device_hash:
            return render_template('login.html', error='Account bound to a different device. Contact your lecturer.')
        session['student_id'] = student.id
        session['student_name'] = student.full_name
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        matric_no = request.form['matric_no']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        parent_phone = request.form['parent_phone']
        device_hash = request.form.get('device_hash', '')
        faculty_id = request.form.get('faculty_id')
        department_id = request.form.get('department_id')
        level = request.form.get('level')
        if Student.query.filter_by(matric_no=matric_no).first():
            return render_template('register.html', error='Matric number already exists')
        if device_hash and Student.query.filter_by(device_hash=device_hash).first():
            return render_template('register.html', error='This device is already registered to another account.')
        student = Student(full_name=full_name, matric_no=matric_no, email=email, password=password,
                          parent_phone=parent_phone, assigned_pattern='circle', device_hash=device_hash,
                          faculty_id=faculty_id, department_id=department_id, level=int(level) if level else None)
        db.session.add(student)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    if request.method == 'POST':
        if not bcrypt.check_password_hash(student.password, request.form['old_password']):
            return render_template('change_password.html', error='Current password is incorrect', role='student')
        student.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
        db.session.commit()
        return render_template('change_password.html', success='Password changed successfully!', role='student')
    return render_template('change_password.html', role='student')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        step = request.form.get('step', '1')
        matric_no = request.form.get('matric_no', '')
        email = request.form.get('email', '')
        token = request.form.get('token', '')
        if step == '1':
            student = Student.query.filter_by(matric_no=matric_no, email=email).first()
            if not student:
                return render_template('forgot_password.html', step='1', error='Matric number and email do not match.')
            t = generate_numeric_token()
            student.reset_token = t
            student.reset_token_expiry = datetime.utcnow() + timedelta(minutes=15)
            db.session.commit()
            return render_template('forgot_password.html', step='2', matric_no=matric_no, email=email, test_token=t,
                                   info=f'Reset code generated. (In production, sent to {email})')
        elif step == '2':
            student = Student.query.filter_by(matric_no=matric_no).first()
            if not student or student.reset_token != token or student.reset_token_expiry < datetime.utcnow():
                return render_template('forgot_password.html', step='2', matric_no=matric_no, email=email, error='Invalid or expired code.')
            student.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
            student.reset_token = None
            student.reset_token_expiry = None
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('forgot_password.html', step='1')

@app.route('/dashboard')
def dashboard():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    enrolled_courses = student.enrolled_courses
    enrolled_course_ids = [c.id for c in enrolled_courses]
    attendances = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.course_id.in_(enrolled_course_ids) if enrolled_course_ids else False
    ).order_by(Attendance.timestamp.desc()).all()
    course_stats = {}
    for course in enrolled_courses:
        course_att = [a for a in attendances if a.course_id == course.id]
        present = len([a for a in course_att if a.status == 'present'])
        absent = len([a for a in course_att if a.status == 'absent'])
        total = present + absent
        rate = round(present / total * 100) if total > 0 else 0
        course_stats[course.code] = {'present': present, 'absent': absent, 'total': total, 'rate': rate, 'sessions': course_att}
    return render_template('student/dashboard.html', student=student, attendances=attendances, course_stats=course_stats)

@app.route('/course_attendance/<course_code>')
def course_attendance(course_code):
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    course = Course.query.filter_by(code=course_code).first()
    if course not in student.enrolled_courses:
        return redirect(url_for('dashboard'))
    attendances = Attendance.query.filter_by(student_id=student.id, course_code=course_code).order_by(Attendance.timestamp.asc()).all()
    total = len(attendances)
    present = len([a for a in attendances if a.status == 'present'])
    rate = round(present / total * 100) if total > 0 else 0
    return render_template('course_attendance.html', student=student, course_code=course_code,
                           attendances=attendances, total=total, present=present, absent=total-present, rate=rate)

@app.route('/my_courses')
def my_courses():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    faculties = Faculty.query.order_by(Faculty.name).all()
    enrolled_ids = [c.id for c in student.enrolled_courses]
    return render_template('enroll_courses.html', student=student, faculties=faculties, enrolled_ids=enrolled_ids)

@app.route('/enroll_course', methods=['POST'])
def enroll_course():
    if 'student_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    course_id = data.get('course_id')
    student = db.session.get(Student, session['student_id'])
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    if course in student.enrolled_courses:
        return jsonify({'success': False, 'message': 'Already enrolled'})
    student.enrolled_courses.append(course)
    db.session.commit()
    return jsonify({'success': True, 'message': f'Enrolled in {course.code}'})

# ---------- Mark Attendance (student) ----------
@app.route('/mark_attendance', methods=['GET', 'POST'])
def mark_attendance():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    if request.method == 'POST':
        data = request.get_json()
        drawing_points = data.get('drawing_points', [])
        canvas_width = data.get('canvas_width', 500)
        canvas_height = data.get('canvas_height', 320)
        device_hash = data.get('device_hash', '')
        session_id = data.get('session_id')
        student_lat = data.get('student_lat')
        student_lng = data.get('student_lng')
        selected_course_id = data.get('course_id')

        device_valid = False
        if student.device_hash is None:
            student.device_hash = device_hash
            db.session.commit()
            device_valid = True
        elif student.device_hash == device_hash:
            device_valid = True

        active_session = ClassSession.query.get(session_id) if session_id else \
                         ClassSession.query.filter_by(is_active=True).order_by(ClassSession.started_at.desc()).first()
        if not active_session or not active_session.is_active:
            return jsonify({'success': False, 'message': 'No active session found'})

        if active_session.course_id != selected_course_id:
            return jsonify({'success': False, 'message': 'Active session is for a different course'})

        if active_session.departments:
            if student.department_id not in [d.id for d in active_session.departments]:
                return jsonify({'success': False, 'message': 'Your department is not included in this session'})

        pattern_valid = validate_pattern_server(drawing_points, canvas_width, canvas_height, active_session.pattern)

        course = active_session.course
        if course not in student.enrolled_courses:
            return jsonify({'success': False, 'message': 'You are not enrolled in this course'})

        server_location_valid = False
        if student_lat and student_lng:
            dist = haversine(float(student_lat), float(student_lng), active_session.lat, active_session.lng)
            server_location_valid = dist <= 50

        if pattern_valid and server_location_valid and device_valid:
            already = Attendance.query.filter_by(student_id=student.id, session_id=active_session.id).first()
            if already:
                return jsonify({'success': False, 'message': 'Already marked for this session'})

            att = Attendance(student_id=student.id, session_id=active_session.id, course_id=course.id,
                             course_code=course.code, status='present', location_valid=server_location_valid,
                             device_valid=device_valid, pattern_valid=pattern_valid)
            db.session.add(att)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return jsonify({'success': False, 'message': 'Attendance already recorded for this session.'})

            try:
                send_attendance_alert(student.full_name, student.parent_phone, course.code, 'present', att.timestamp)
            except Exception as e:
                print(f"SMS error: {e}")
            return jsonify({'success': True, 'message': f'Attendance marked for {course.code}!'})
        else:
            reasons = []
            if not pattern_valid: reasons.append('Invalid pattern')
            if not server_location_valid: reasons.append('Not within classroom range')
            if not device_valid: reasons.append('Unrecognized device')
            return jsonify({'success': False, 'message': ', '.join(reasons)})

    enrolled_courses = student.enrolled_courses
    return render_template('mark_attendance.html', student=student, enrolled_courses=enrolled_courses)

@app.route('/get_session')
def get_session():
    active = ClassSession.query.filter_by(is_active=True).order_by(ClassSession.started_at.desc()).first()
    if active:
        return jsonify({'success': True, 'session_id': active.id, 'pattern': active.pattern,
                        'lat': active.lat, 'lng': active.lng, 'course_code': active.course.code,
                        'course_title': active.course.title, 'course_id': active.course.id})
    return jsonify({'success': False, 'message': 'No active session'})

# ---------- Lecturer Routes ----------
@app.route('/lecturer/login', methods=['GET', 'POST'])
def lecturer_login():
    if request.method == 'POST':
        lecturer = Lecturer.query.filter_by(email=request.form['email']).first()
        if lecturer and bcrypt.check_password_hash(lecturer.password, request.form['password']):
            session['lecturer_id'] = lecturer.id
            session['lecturer_name'] = lecturer.full_name
            return redirect(url_for('lecturer_dashboard'))
        return render_template('lecturer_login.html', error='Invalid email or password')
    return render_template('lecturer_login.html')

@app.route('/lecturer/register', methods=['GET', 'POST'])
def lecturer_register():
    if request.method == 'POST':
        email = request.form['email']
        if Lecturer.query.filter_by(email=email).first():
            return render_template('lecturer_register.html', error='Email already exists', faculties=Faculty.query.all())
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        department_id = request.form.get('department_id') or None
        lecturer = Lecturer(full_name=request.form['full_name'], email=email, password=password,
                            department_id=department_id)
        db.session.add(lecturer)
        db.session.commit()
        return redirect(url_for('lecturer_login'))
    faculties = Faculty.query.order_by(Faculty.name).all()
    return render_template('lecturer_register.html', faculties=faculties)

@app.route('/lecturer/logout')
def lecturer_logout():
    session.clear()
    return redirect(url_for('lecturer_login'))

@app.route('/lecturer/change_password', methods=['GET', 'POST'])
def lecturer_change_password():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    if request.method == 'POST':
        if not bcrypt.check_password_hash(lecturer.password, request.form['old_password']):
            return render_template('change_password.html', error='Current password is incorrect', role='lecturer')
        lecturer.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
        db.session.commit()
        return render_template('change_password.html', success='Password changed!', role='lecturer')
    return render_template('change_password.html', role='lecturer')

@app.route('/lecturer/forgot_password', methods=['GET', 'POST'])
def lecturer_forgot_password():
    if request.method == 'POST':
        step = request.form.get('step', '1')
        email = request.form.get('email', '')
        token = request.form.get('token', '')
        if step == '1':
            lecturer = Lecturer.query.filter_by(email=email).first()
            if not lecturer:
                return render_template('forgot_password.html', step='1', error='Email not found.', role='lecturer')
            t = generate_numeric_token()
            lecturer.reset_token = t
            lecturer.reset_token_expiry = datetime.utcnow() + timedelta(minutes=15)
            db.session.commit()
            return render_template('forgot_password.html', step='2', email=email, test_token=t,
                                   info=f'Code generated. (In production, sent to {email})', role='lecturer')
        elif step == '2':
            lecturer = Lecturer.query.filter_by(email=email).first()
            if not lecturer or lecturer.reset_token != token or lecturer.reset_token_expiry < datetime.utcnow():
                return render_template('forgot_password.html', step='2', email=email, error='Invalid or expired code.', role='lecturer')
            lecturer.password = bcrypt.generate_password_hash(request.form['new_password']).decode('utf-8')
            lecturer.reset_token = None
            lecturer.reset_token_expiry = None
            db.session.commit()
            return redirect(url_for('lecturer_login'))
    return render_template('forgot_password.html', step='1', role='lecturer')

@app.route('/lecturer/dashboard')
def lecturer_dashboard():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    my_courses = [lc.course for lc in lecturer.lecturer_courses]

    student_ids = set()
    for course in my_courses:
        for student in course.enrolled_students:
            student_ids.add(student.id)
    total_students = len(student_ids)
    total_sessions = ClassSession.query.filter_by(lecturer_id=lecturer.id).count()
    courses_count = len(my_courses)

    return render_template('lecturer/dashboard.html',
                           lecturer=lecturer,
                           my_courses=my_courses,
                           total_students=total_students,
                           total_sessions=total_sessions,
                           courses_count=courses_count)

@app.route('/lecturer/set_session', methods=['POST'])
def set_session():
    if 'lecturer_id' not in session:
        return jsonify({'success': False})
    data = request.get_json()
    pattern = data.get('pattern')
    lat = data.get('lat')
    lng = data.get('lng')
    course_id = data.get('course_id')
    department_ids = data.get('department_ids', [])

    for s in ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).all():
        s.is_active = False
        s.ended_at = datetime.utcnow()

    if pattern and lat and lng and course_id and department_ids:
        new_session = ClassSession(lecturer_id=session['lecturer_id'], course_id=int(course_id),
                                   pattern=pattern, lat=lat, lng=lng, is_active=True, started_at=datetime.utcnow())
        for dept_id in department_ids:
            dept = db.session.get(Department, int(dept_id))
            if dept:
                new_session.departments.append(dept)
        db.session.add(new_session)
        Student.query.update({'assigned_pattern': pattern})
        db.session.commit()
    else:
        db.session.commit()
    return jsonify({'success': True})

@app.route('/lecturer/mark_absent', methods=['POST'])
def mark_absent():
    if 'lecturer_id' not in session:
        return jsonify({'success': False})
    data = request.get_json()
    session_id = data.get('session_id')
    active_session = ClassSession.query.get(session_id) if session_id else \
                     ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).first()
    if not active_session:
        return jsonify({'success': False, 'message': 'No active session'})

    allowed_dept_ids = [d.id for d in active_session.departments] if active_session.departments else None
    absent_count = 0
    existing_ids = [a.student_id for a in Attendance.query.filter_by(session_id=active_session.id).all()]

    course = active_session.course
    for student in course.enrolled_students:
        if student.id in existing_ids:
            continue
        if allowed_dept_ids is not None and student.department_id not in allowed_dept_ids:
            continue
        try:
            db.session.add(Attendance(
                student_id=student.id, session_id=active_session.id,
                course_id=course.id, course_code=course.code,
                status='absent', location_valid=False, device_valid=False, pattern_valid=False))
            absent_count += 1
            try:
                send_attendance_alert(student.full_name, student.parent_phone, course.code, 'absent', datetime.utcnow())
            except:
                pass
        except IntegrityError:
            db.session.rollback()
            continue

    active_session.is_active = False
    active_session.ended_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': f'{absent_count} students marked absent. Session ended.'})

# ---------- Lecturer Course Management ----------
@app.route('/lecturer/my_courses')
def lecturer_my_courses():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'courses': []})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    courses = []
    for lc in lecturer.lecturer_courses:
        courses.append({
            'id': lc.course.id,
            'code': lc.course.code,
            'title': lc.course.title,
            'level': lc.course.level,
            'department': lc.course.department.name if lc.course.department else 'N/A'
        })
    return jsonify({'success': True, 'courses': courses})

@app.route('/lecturer/add_course', methods=['POST'])
def lecturer_add_course():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    course_id = data.get('course_id')
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    if LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course.id).first():
        return jsonify({'success': False, 'message': 'Course already assigned'})
    lc = LecturerCourse(lecturer_id=lecturer.id, course_id=course.id)
    db.session.add(lc)
    db.session.commit()
    return jsonify({'success': True, 'message': f'Course {course.code} added to your profile'})

@app.route('/lecturer/remove_course', methods=['POST'])
def lecturer_remove_course():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    course_id = data.get('course_id')
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    lc = LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first()
    if not lc:
        return jsonify({'success': False, 'message': 'Course not in your list'})
    db.session.delete(lc)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Course removed'})

# ---------- Lecturer Student & Attendance Details ----------
@app.route('/lecturer/enrolled_students/<int:course_id>')
def lecturer_enrolled_students(course_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    if not LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'You do not teach this course'})
    students = course.enrolled_students.order_by(Student.matric_no).all()
    result = []
    for s in students:
        atts = Attendance.query.filter_by(student_id=s.id, course_id=course_id).all()
        total = len(atts)
        present = len([a for a in atts if a.status == 'present'])
        result.append({
            'id': s.id,
            'name': s.full_name,
            'matric_no': s.matric_no,
            'department': s.department_rel.name if s.department_rel else 'N/A',
            'level': s.level or 'N/A',
            'present': present,
            'absent': total - present,
            'total': total,
            'rate': round(present/total*100) if total > 0 else 0,
            'device_bound': s.device_hash is not None
        })
    return jsonify({'success': True, 'students': result})

@app.route('/lecturer/student_detail/<int:student_id>/<int:course_id>')
def lecturer_student_detail(student_id, course_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    if not LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'You do not teach this course'})
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    if course not in student.enrolled_courses:
        return jsonify({'success': False, 'message': 'Student not enrolled in this course'})
    attendances = Attendance.query.filter_by(student_id=student_id, course_id=course_id).order_by(Attendance.timestamp.asc()).all()
    sessions_data = [{
        'date': a.timestamp.strftime('%d %b %Y %I:%M %p'),
        'status': a.status,
        'location_valid': a.location_valid,
        'device_valid': a.device_valid,
        'pattern_valid': a.pattern_valid
    } for a in attendances]
    return jsonify({
        'success': True,
        'student': {
            'id': student.id,
            'full_name': student.full_name,
            'matric_no': student.matric_no,
            'email': student.email,
            'parent_phone': student.parent_phone,
            'department': student.department_rel.name if student.department_rel else 'N/A',
            'level': student.level or 'N/A',
            'faculty': student.faculty.name if student.faculty else 'N/A',
            'device_bound': student.device_hash is not None
        },
        'attendances': sessions_data
    })

@app.route('/lecturer/course_sessions/<int:course_id>')
def lecturer_course_sessions(course_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    if not LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'You do not teach this course'})

    course = db.session.get(Course, course_id)
    total_enrolled = course.enrolled_students.count()
    dept_filter = request.args.get('department_id', type=int)

    sessions_query = ClassSession.query.filter_by(course_id=course_id)
    if dept_filter:
        sessions_query = sessions_query.join(session_departments).filter(
            session_departments.c.department_id == dept_filter
        )

    sessions = sessions_query.order_by(ClassSession.started_at.desc()).all()

    session_list = []
    for s in sessions:
        present = Attendance.query.filter_by(session_id=s.id, status='present').count()
        if s.departments:
            dept_ids = [d.id for d in s.departments]
            total = db.session.query(Student).join(StudentCourse).filter(
                StudentCourse.course_id == course_id,
                Student.id == StudentCourse.student_id,
                Student.department_id.in_(dept_ids)
            ).count()
        else:
            total = total_enrolled
        absent = total - present
        session_list.append({
            'id': s.id,
            'pattern': s.pattern,
            'lat': s.lat,
            'lng': s.lng,
            'started_at': s.started_at.strftime('%d %b %Y %I:%M %p'),
            'ended_at': s.ended_at.strftime('%d %b %Y %I:%M %p') if s.ended_at else 'Ongoing',
            'is_active': s.is_active,
            'total': total,
            'present': present,
            'absent': absent,
            'department_ids': [d.id for d in s.departments]
        })
    return jsonify({'success': True, 'sessions': session_list})

@app.route('/lecturer/session_attendance/<int:session_id>')
def lecturer_session_attendance(session_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    session_obj = ClassSession.query.get(session_id)
    if not session_obj:
        return jsonify({'success': False, 'message': 'Session not found'})
    if not LecturerCourse.query.filter_by(lecturer_id=session['lecturer_id'],
                                          course_id=session_obj.course_id).first():
        return jsonify({'success': False, 'message': 'Unauthorized'})

    course = session_obj.course
    enrolled_students = course.enrolled_students.order_by(Student.matric_no).all()
    status_filter = request.args.get('status', '').lower()
    dept_filter = request.args.get('department_id', type=int)

    present_records = Attendance.query.filter_by(session_id=session_id, status='present').all()
    present_student_ids = {rec.student_id for rec in present_records}

    records = []
    for student in enrolled_students:
        if dept_filter and student.department_id != dept_filter:
            continue
        status = 'present' if student.id in present_student_ids else 'absent'
        if status_filter and status != status_filter:
            continue
        records.append({
            'student_name': student.full_name,
            'matric_no': student.matric_no,
            'status': status,
            'department': student.department_rel.name if student.department_rel else 'N/A'
        })
    return jsonify({'success': True, 'records': records})

@app.route('/lecturer/download_attendance/<int:course_id>')
def download_attendance(course_id):
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    course = db.session.get(Course, course_id)
    if not course:
        return "Course not found", 404

    department_id = request.args.get('department_id', type=int)
    students_query = course.enrolled_students.order_by(Student.matric_no)
    if department_id:
        students_query = students_query.filter(Student.department_id == department_id)
    students = students_query.all()

    sessions_query = ClassSession.query.filter_by(course_id=course_id)
    if department_id:
        sessions_query = sessions_query.join(session_departments).filter(
            session_departments.c.department_id == department_id)
    sessions = sessions_query.order_by(ClassSession.started_at).all()

    lecturer_names = [lc.lecturer.full_name for lc in course.lecturer_courses]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Course Code', course.code])
    writer.writerow(['Course Title', course.title])
    writer.writerow(['Department', course.department.name])
    writer.writerow(['Lecturer(s)', ', '.join(lecturer_names)])
    writer.writerow([])

    header = ['S/N', 'Matric No', 'Full Name', 'Level', 'Department']
    for i, s in enumerate(sessions):
        header.append(f"Session {i+1}\n{s.started_at.strftime('%d/%m/%y')}")
    header += ['Total Present', 'Total Absent', 'Rate (%)']
    writer.writerow(header)

    for i, student in enumerate(students, 1):
        dept = Department.query.get(student.department_id) if student.department_id else None
        row = [i, student.matric_no, student.full_name, student.level or '', dept.name if dept else '']
        tp = 0
        for s in sessions:
            att = Attendance.query.filter_by(student_id=student.id, session_id=s.id).first()
            if att:
                row.append('P' if att.status == 'present' else 'A')
                if att.status == 'present':
                    tp += 1
            else:
                row.append('-')
        tot = len(sessions)
        ab = tot - tp
        row += [tp, ab, f'{round(tp/tot*100) if tot > 0 else 0}%']
        writer.writerow(row)

    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True,
                     download_name=f"{course.code}_attendance_{datetime.now().strftime('%Y%m%d')}.csv")

# ---------- Page routes (for lecturer) ----------
@app.route('/lecturer/courses')
def lecturer_courses_page():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    return render_template('lecturer/courses.html')

@app.route('/lecturer/students')
def lecturer_students_page():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    return render_template('lecturer/students.html')

@app.route('/lecturer/records')
def lecturer_records_page():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    return render_template('lecturer/records.html')

# ---------- Admin routes (unchanged) ----------
# ... (keep the admin routes you already have) ...

if __name__ == '__main__':
    app.run(debug=True)