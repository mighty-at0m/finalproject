from dotenv import load_dotenv
load_dotenv()
import os
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, abort
import logging
from logging.handlers import RotatingFileHandler
from functools import wraps
from flask_bcrypt import Bcrypt
from models import db, Student, Lecturer, Attendance, ClassSession, Faculty, Department, Course, LecturerCourse, Admin, StudentCourse, session_departments
from datetime import datetime, timedelta
import io, csv, string
from sms_service import send_attendance_alert, send_absence_warning
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

# ---- Logging setup (rotating file) ----
logger = logging.getLogger('smart_attendance')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('app.log', maxBytes=5*1024*1024, backupCount=3)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
if app.debug:
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    logger.addHandler(console)

app.config['PROPAGATE_EXCEPTIONS'] = True

# ---- Temporary development secrets (REMOVE FOR PRODUCTION) ----
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-later')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')   # use '' if your MySQL root has no password
# ----------------------------------------------------------------

bcrypt = Bcrypt(app)

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+mysqlconnector://root:{DB_PASSWORD}@localhost/smart_attendance'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

# ── Helper classes ──
class AttendanceRecord:
    """Lightweight object to mimic Attendance for templates."""
    def __init__(self, timestamp, status):
        self.timestamp = timestamp
        self.status = status

# ── Helper functions ──
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

def mark_absent_for_session(session_obj):
    """Create absent Attendance records for all eligible students not yet recorded."""
    allowed_dept_ids = [d.id for d in session_obj.departments] if session_obj.departments else None
    existing_ids = [a.student_id for a in Attendance.query.filter_by(session_id=session_obj.id).all()]
    absent_count = 0

    for student in session_obj.course.enrolled_students:
        if student.id in existing_ids:
            continue
        if allowed_dept_ids is not None and student.department_id not in allowed_dept_ids:
            continue
        try:
            db.session.add(Attendance(
                student_id=student.id,
                session_id=session_obj.id,
                course_id=session_obj.course_id,
                course_code=session_obj.course.code,
                status='absent',
                location_valid=False,
                device_valid=False,
                pattern_valid=False
            ))
            absent_count += 1
        except IntegrityError:
            db.session.rollback()
    return absent_count

def notify_absent_students_for_session(session_obj):
    """Send absence SMS for students marked absent in a specific session."""
    absences = Attendance.query.filter_by(session_id=session_obj.id, status='absent').all()
    sent = 0
    failed = 0
    last_error = None
    for att in absences:
        student = db.session.get(Student, att.student_id)
        if not student or not student.parent_phone:
            continue
        try:
            ok, detail = send_attendance_alert(
                student.full_name,
                student.parent_phone,
                session_obj.course.code,
                'absent',
                att.timestamp or datetime.utcnow()
            )
            if ok:
                sent += 1
            else:
                failed += 1
                last_error = detail
        except Exception as e:
            logger.exception(f"Absence SMS error for student {att.student_id}: {e}")
            failed += 1
            last_error = str(e)
    return sent, failed, last_error

def notify_absent_students_for_session_id(session_id):
    """Send absence SMS using a session id after DB commit."""
    sess = db.session.get(ClassSession, session_id)
    if not sess:
        return 0, 0, 'Session not found for SMS notification'
    return notify_absent_students_for_session(sess)


def get_json_data():
    """Safely get JSON data or abort with 400."""
    data = request.get_json(silent=True)
    if data is None:
        abort(400, description='Invalid or missing JSON')
    return data


def login_required(role='student'):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if role == 'student' and 'student_id' not in session:
                if request.is_json:
                    return jsonify({'success': False, 'message': 'Unauthorized'}), 401
                return redirect(url_for('login'))
            if role == 'lecturer' and 'lecturer_id' not in session:
                if request.is_json:
                    return jsonify({'success': False, 'message': 'Unauthorized'}), 401
                return redirect(url_for('lecturer_login'))
            if role == 'admin' and 'admin_id' not in session:
                if request.is_json:
                    return jsonify({'success': False, 'message': 'Unauthorized'}), 401
                return redirect(url_for('admin_login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def db_transaction(f):
    """Decorator to wrap routes that mutate the DB. Commits on success,
    rolls back and logs on exception, and returns a JSON or HTML 500 response.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            result = f(*args, **kwargs)
            try:
                db.session.commit()
            except Exception:
                # commit may be handled inside the route; ignore commit errors here
                pass
            return result
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.exception(f"Unhandled exception in {f.__name__}: {e}")
            if request.is_json:
                return jsonify({'success': False, 'message': 'Internal server error. Please try again later.'}), 500
            return render_template('errors/500.html'), 500
    return wrapper

# ────────────── API endpoints ──────────────
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

# ────────────── Student routes ──────────────
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        matric_no = request.form['matric_no']
        password = request.form['password']
        device_token = request.form.get('device_token', '')
        student = Student.query.filter_by(matric_no=matric_no).first()
        if not student or not bcrypt.check_password_hash(student.password, password):
            return render_template('login.html', error='Invalid matric number or password')
        if student.device_token is not None:
            if device_token != student.device_token:
                return render_template('login.html', error='Account bound to a different device. Contact your lecturer or admin to reset it.')
        else:
            # First login – generate token
            student.device_token = secrets.token_hex(32)
            db.session.commit()
            return redirect(url_for('set_device_token', token=student.device_token, next=url_for('dashboard')))
        session['student_id'] = student.id
        session['student_name'] = student.full_name
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/set_device_token')
def set_device_token():
    token = request.args.get('token')
    redirect_url = request.args.get('next', url_for('dashboard'))
    return render_template('set_device_token.html', token=token, redirect=redirect_url)

@app.route('/register', methods=['GET', 'POST'])
@db_transaction
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        matric_no = request.form['matric_no']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        parent_phone = request.form['parent_phone']
        faculty_id = request.form.get('faculty_id')
        department_id = request.form.get('department_id')
        level = request.form.get('level')
        if Student.query.filter_by(matric_no=matric_no).first():
            return render_template('register.html', error='Matric number already exists')
        student = Student(full_name=full_name, matric_no=matric_no, email=email, password=password,
                          parent_phone=parent_phone, assigned_pattern='circle',
                          faculty_id=faculty_id, department_id=department_id, level=int(level) if level else None)
        db.session.add(student)
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

    # Recalculate course stats & overall totals from sessions
    course_stats = {}
    total_eligible_sessions = 0
    total_present_overall = 0

    for course in enrolled_courses:
        sessions = ClassSession.query.filter_by(course_id=course.id).all()
        present = 0
        course_eligible = 0
        for sess in sessions:
            eligible = True
            if sess.departments:
                eligible = student.department_id in [d.id for d in sess.departments]
            if not eligible:
                continue
            course_eligible += 1
            if Attendance.query.filter_by(student_id=student.id, session_id=sess.id, status='present').first():
                present += 1

        total_eligible_sessions += course_eligible
        total_present_overall += present
        absent = course_eligible - present
        rate = round(present / course_eligible * 100) if course_eligible > 0 else 0
        course_stats[course.code] = {
            'present': present,
            'absent': absent,
            'total': course_eligible,
            'rate': rate
        }

    # We still need recent attendances for the history list (unchanged)
    enrolled_course_ids = [c.id for c in enrolled_courses]
    recent_attendances = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.course_id.in_(enrolled_course_ids) if enrolled_course_ids else False
    ).order_by(Attendance.timestamp.desc()).limit(10).all()

    return render_template('student/dashboard.html',
                           student=student,
                           total_eligible_sessions=total_eligible_sessions,
                           total_present_overall=total_present_overall,
                           course_stats=course_stats,
                           attendances=recent_attendances)

@app.route('/course_attendance/<course_code>')
def course_attendance(course_code):
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    course = Course.query.filter_by(code=course_code).first()
    if not course or course not in student.enrolled_courses:
        return redirect(url_for('dashboard'))

    sessions = ClassSession.query.filter_by(course_id=course.id).order_by(ClassSession.started_at.asc()).all()

    present_count = 0
    total_eligible = 0
    attendance_records = []

    for sess in sessions:
        eligible = True
        if sess.departments:
            eligible = student.department_id in [d.id for d in sess.departments]
        if not eligible:
            continue
        total_eligible += 1
        att = Attendance.query.filter_by(student_id=student.id, session_id=sess.id, status='present').first()
        status = 'present' if att else 'absent'
        timestamp = att.timestamp if att else sess.started_at
        attendance_records.append(AttendanceRecord(timestamp, status))
        if status == 'present':
            present_count += 1

    absent_count = total_eligible - present_count
    rate = round(present_count / total_eligible * 100) if total_eligible > 0 else 0

    return render_template('course_attendance.html',
                           student=student,
                           course_code=course_code,
                           attendances=attendance_records,
                           total=total_eligible,
                           present=present_count,
                           absent=absent_count,
                           rate=rate)

@app.route('/my_courses')
def my_courses():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    faculties = Faculty.query.order_by(Faculty.name).all()
    enrolled_ids = [c.id for c in student.enrolled_courses]
    return render_template('enroll_courses.html',
                           student=student,
                           faculties=faculties,
                           enrolled_ids=enrolled_ids,
                           enrolled_courses=student.enrolled_courses)   # <-- ADD THIS

@app.route('/enroll_course', methods=['POST'])
@db_transaction
def enroll_course():
    if 'student_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = get_json_data()
    course_id = data.get('course_id')
    student = db.session.get(Student, session['student_id'])
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    if course in student.enrolled_courses:
        return jsonify({'success': False, 'message': 'Already enrolled'})
    student.enrolled_courses.append(course)
    return jsonify({'success': True, 'message': f'Enrolled in {course.code}'})

@app.route('/admin/remove_student_course', methods=['POST'])
@db_transaction
def admin_remove_student_course():
    if 'admin_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'})
    data = get_json_data()
    student = db.session.get(Student, data.get('student_id'))
    course = db.session.get(Course, data.get('course_id'))
    if not student or not course:
        return jsonify({'success': False, 'message': 'Invalid student or course'})
    if course in student.enrolled_courses:
        student.enrolled_courses.remove(course)
        return jsonify({'success': True, 'message': f'Removed {student.full_name} from {course.code}'})
    return jsonify({'success': False, 'message': 'Student not enrolled in this course'})

@app.route('/lecturer/remove_student_course', methods=['POST'])
@db_transaction
def lecturer_remove_student_course():
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'})
    data = get_json_data()
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, data.get('course_id'))
    if not course or course not in [lc.course for lc in lecturer.lecturer_courses]:
        return jsonify({'success': False, 'message': 'You do not teach this course'})
    student = db.session.get(Student, data.get('student_id'))
    if not student: return jsonify({'success': False, 'message': 'Student not found'})
    if course in student.enrolled_courses:
        student.enrolled_courses.remove(course)
        return jsonify({'success': True, 'message': f'Removed {student.full_name} from {course.code}'})
    return jsonify({'success': False, 'message': 'Student not enrolled in this course'})

# ────────────── Mark Attendance (student) ──────────────
@app.route('/mark_attendance', methods=['GET', 'POST'])
@login_required(role='student')
def mark_attendance():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = db.session.get(Student, session['student_id'])
    if request.method == 'POST':
        data = get_json_data()
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
                logger.exception(f"SMS error sending attendance alert for student {student.id}: {e}")
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

# ────────────── Lecturer routes ──────────────
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
@db_transaction
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
@login_required(role='lecturer')
def lecturer_dashboard():
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

# ── Session management ──
@app.route('/lecturer/set_session', methods=['POST'])
@login_required(role='lecturer')
def set_session():
    data = get_json_data()
    pattern = data.get('pattern')
    lat = data.get('lat')
    lng = data.get('lng')
    course_id = data.get('course_id')
    department_ids = data.get('department_ids') or []
    try:
        ended_session_ids = []
        # End any active session for this lecturer, marking absentees first
        for s in ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).all():
            mark_absent_for_session(s)   # <-- automatically records absentees
            s.is_active = False
            s.ended_at = datetime.utcnow()
            ended_session_ids.append(s.id)

        if pattern and lat and lng and course_id and department_ids:
            new_session = ClassSession(lecturer_id=session['lecturer_id'], course_id=int(course_id),
                                       pattern=pattern, lat=lat, lng=lng, is_active=True, started_at=datetime.utcnow())
            for dept_id in department_ids:
                dept = db.session.get(Department, int(dept_id))
                if dept:
                    new_session.departments.append(dept)
            db.session.add(new_session)
            db.session.flush()
            session_id = new_session.id
            Student.query.update({'assigned_pattern': pattern})
            db.session.commit()
        else:
            session_id = None
            db.session.commit()
        # Send SMS after commit so session end is never blocked.
        for sid in ended_session_ids:
            try:
                notify_absent_students_for_session_id(sid)
            except Exception as e:
                logger.exception(f"Post-commit SMS notify error for session {sid}: {e}")
        return jsonify({'success': True, 'session_id': session_id})
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Set session error: {e}")
        return jsonify({'success': False, 'message': 'Failed to update session. Please try again later.'}), 500

@app.route('/lecturer/mark_absent', methods=['POST'])
@login_required(role='lecturer')
def mark_absent():
    data = get_json_data()
    session_id = data.get('session_id')
    try:
        active_session = db.session.get(ClassSession, session_id) if session_id else \
                         ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).first()
        if not active_session:
            return jsonify({'success': False, 'message': 'No active session'})

        absent_count = mark_absent_for_session(active_session)
        active_session.is_active = False
        active_session.ended_at = datetime.utcnow()
        ended_session_id = active_session.id
        db.session.commit()
        sent_count, failed_count, sms_error = notify_absent_students_for_session_id(ended_session_id)
        message = (
            f'{absent_count} students marked absent. Session ended. '
            f'SMS sent: {sent_count}'
            + (f', failed: {failed_count}' if failed_count else '')
        )
        if failed_count and sms_error:
            message += f'. Last SMS error: {sms_error}'
        return jsonify({
            'success': True,
            'message': message,
            'sms': {
                'sent': sent_count,
                'failed': failed_count,
                'last_error': sms_error
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Mark absent error: {e}")
        return jsonify({'success': False, 'message': 'Failed to end session. Please try again later.'}), 500

# ── Lecturer search / reset device / all students ──
@app.route('/lecturer/search_student')
def search_student():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    matric_no = request.args.get('matric_no', '')
    course_id = request.args.get('course_id')
    student = Student.query.filter_by(matric_no=matric_no).first()
    if not student: return jsonify({'success': False, 'message': 'Student not found'})
    query = Attendance.query.filter_by(student_id=student.id)
    if course_id: query = query.filter_by(course_id=int(course_id))
    attendances = query.order_by(Attendance.timestamp.asc()).all()
    total = len(attendances)
    present = len([a for a in attendances if a.status == 'present'])
    dept = Department.query.get(student.department_id) if student.department_id else None
    fac = Faculty.query.get(student.faculty_id) if student.faculty_id else None
    sessions_list = [{'date': a.timestamp.strftime('%d %b %Y'), 'time': a.timestamp.strftime('%I:%M %p'),
                      'status': a.status, 'course': a.course_code} for a in attendances]
    return jsonify({'success': True,
        'student': {'name': student.full_name, 'matric_no': student.matric_no, 'email': student.email,
                    'parent_phone': student.parent_phone, 'faculty': fac.name if fac else 'N/A',
                    'department': dept.name if dept else 'N/A', 'level': student.level or 'N/A',
                    'device_bound': student.device_hash is not None, 'id': student.id},
        'stats': {'total': total, 'present': present, 'absent': total-present,
                  'rate': round(present/total*100) if total > 0 else 0},
        'sessions': sessions_list})

@app.route('/lecturer/reset_device/<int:student_id>', methods=['POST'])
@db_transaction
def reset_device(student_id):
    if 'lecturer_id' not in session: return jsonify({'success': False})
    student = db.session.get(Student, student_id)
    if not student: return jsonify({'success': False, 'message': 'Student not found'})
    student.device_hash = None
    student.device_token = None
    return jsonify({'success': True, 'message': f'Device reset for {student.full_name}'})

@app.route('/lecturer/all_students')
def all_students():
    if 'lecturer_id' not in session: return jsonify({'success': False})
    result = []
    for s in Student.query.all():
        atts = Attendance.query.filter_by(student_id=s.id).all()
        total = len(atts)
        present = len([a for a in atts if a.status == 'present'])
        dept = Department.query.get(s.department_id) if s.department_id else None
        result.append({'id': s.id, 'name': s.full_name, 'matric_no': s.matric_no,
                       'department': dept.name if dept else 'N/A', 'level': s.level or 'N/A',
                       'present': present, 'absent': total-present, 'total': total,
                       'rate': round(present/total*100) if total > 0 else 0,
                       'device_bound': s.device_hash is not None})
    return jsonify({'success': True, 'students': result})

# ── Lecturer course management ──
@app.route('/lecturer/my_courses')
def lecturer_my_courses():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'courses': []})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    courses = [{'id': lc.course.id, 'code': lc.course.code, 'title': lc.course.title,
                'level': lc.course.level, 'department': lc.course.department.name}
               for lc in lecturer.lecturer_courses]
    return jsonify({'success': True, 'courses': courses})

@app.route('/lecturer/add_course', methods=['POST'])
@db_transaction
def lecturer_add_course():
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Not logged in'})
    data = get_json_data()
    course_id = data.get('course_id')
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course: return jsonify({'success': False, 'message': 'Course not found'})
    if LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course.id).first():
        return jsonify({'success': False, 'message': 'Course already assigned'})
    lc = LecturerCourse(lecturer_id=lecturer.id, course_id=course.id)
    db.session.add(lc)
    return jsonify({'success': True, 'message': f'Course {course.code} added to your profile'})

@app.route('/lecturer/remove_course', methods=['POST'])
@db_transaction
def lecturer_remove_course():
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Not logged in'})
    data = get_json_data()
    course_id = data.get('course_id')
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    lc = LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first()
    if not lc: return jsonify({'success': False, 'message': 'Course not in your list'})
    db.session.delete(lc)
    return jsonify({'success': True, 'message': 'Course removed'})

# ── Lecturer student & attendance details ──
@app.route('/lecturer/enrolled_students/<int:course_id>')
def lecturer_enrolled_students(course_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course or not LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'Course not found or not taught by you'})

    students = course.enrolled_students.order_by(Student.matric_no).all()
    sessions = ClassSession.query.filter_by(course_id=course_id).all()

    result = []
    for s in students:
        total_eligible = 0
        present_count = 0
        for sess in sessions:
            eligible = True
            if sess.departments:
                eligible = s.department_id in [d.id for d in sess.departments]
            if not eligible:
                continue
            total_eligible += 1
            att = Attendance.query.filter_by(student_id=s.id, session_id=sess.id, status='present').first()
            if att:
                present_count += 1
        absent_count = total_eligible - present_count
        rate = round(present_count / total_eligible * 100) if total_eligible > 0 else 0

        result.append({
            'id': s.id,
            'name': s.full_name,
            'matric_no': s.matric_no,
            'department': s.department_rel.name if s.department_rel else 'N/A',
            'level': s.level or 'N/A',
            'present': present_count,
            'absent': absent_count,
            'total': total_eligible,
            'rate': rate,
            'device_bound': s.device_hash is not None
        })

    return jsonify({'success': True, 'students': result})

@app.route('/lecturer/student_detail/<int:student_id>/<int:course_id>')
def lecturer_student_detail(student_id, course_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, session['lecturer_id'])
    course = db.session.get(Course, course_id)
    if not course or not LecturerCourse.query.filter_by(lecturer_id=lecturer.id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'Course not found or not taught by you'})

    student = db.session.get(Student, student_id)
    if not student or course not in student.enrolled_courses:
        return jsonify({'success': False, 'message': 'Student not enrolled in this course'})

    sessions = ClassSession.query.filter_by(course_id=course_id).order_by(ClassSession.started_at.asc()).all()

    sessions_data = []
    for sess in sessions:
        eligible = True
        if sess.departments:
            eligible = student.department_id in [d.id for d in sess.departments]
        if not eligible:
            continue
        att = Attendance.query.filter_by(student_id=student_id, session_id=sess.id, status='present').first()
        status = 'present' if att else 'absent'
        timestamp = att.timestamp if att else sess.started_at
        sessions_data.append({
            'date': timestamp.strftime('%d %b %Y %I:%M %p'),
            'status': status,
            'location_valid': att.location_valid if att else False,
            'device_valid': att.device_valid if att else False,
            'pattern_valid': att.pattern_valid if att else False
        })

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

# ── Course sessions & attendance drill-down ──
@app.route('/lecturer/course_sessions/<int:course_id>')
def lecturer_course_sessions(course_id):
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'})
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
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'})
    session_obj = db.session.get(ClassSession, session_id)
    if not session_obj: return jsonify({'success': False, 'message': 'Session not found'})
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
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    course = db.session.get(Course, course_id)
    if not course: return "Course not found", 404

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

# ── Real‑time session endpoints ──
@app.route('/lecturer/active_session')
def lecturer_active_session():
    if 'lecturer_id' not in session:
        return jsonify({'active': False})
    active = ClassSession.query.filter_by(lecturer_id=session['lecturer_id'], is_active=True).first()
    if not active:
        return jsonify({'active': False})
    course = active.course
    present = Attendance.query.filter_by(session_id=active.id, status='present').count()
    allowed_dept_ids = [d.id for d in active.departments] if active.departments else None
    if allowed_dept_ids:
        total = db.session.query(Student).join(StudentCourse).filter(
            StudentCourse.course_id == course.id,
            Student.id == StudentCourse.student_id,
            Student.department_id.in_(allowed_dept_ids)
        ).count()
    else:
        total = course.enrolled_students.count()
    return jsonify({
        'active': True,
        'session_id': active.id,
        'course_code': course.code,
        'course_title': course.title,
        'pattern': active.pattern,
        'lat': active.lat,
        'lng': active.lng,
        'present': present,
        'total': total
    })

@app.route('/lecturer/session_status/<int:session_id>')
def lecturer_session_status(session_id):
    if 'lecturer_id' not in session: return jsonify({'success': False, 'message': 'Unauthorized'})
    sess = db.session.get(ClassSession, session_id)
    if not sess: return jsonify({'success': False, 'message': 'Session not found'})
    if not sess.is_active:
        return jsonify({'success': False, 'ended': True, 'message': 'Session has ended'})
    present_records = Attendance.query.filter_by(session_id=session_id, status='present').order_by(Attendance.timestamp.asc()).all()
    present_students = []
    for att in present_records:
        s = att.student
        present_students.append({
            'name': s.full_name,
            'matric_no': s.matric_no,
            'timestamp': att.timestamp.strftime('%I:%M:%S %p')
        })
    course = sess.course
    allowed_dept_ids = [d.id for d in sess.departments] if sess.departments else None
    if allowed_dept_ids:
        total = db.session.query(Student).join(StudentCourse).filter(
            StudentCourse.course_id == course.id,
            Student.id == StudentCourse.student_id,
            Student.department_id.in_(allowed_dept_ids)
        ).count()
    else:
        total = course.enrolled_students.count()
    return jsonify({
        'success': True,
        'present': len(present_students),
        'total': total,
        'present_students': present_students
    })

# ── Page routes ──
@app.route('/lecturer/courses')
def lecturer_courses_page():
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    return render_template('lecturer/courses.html')

@app.route('/lecturer/students')
def lecturer_students_page():
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    return render_template('lecturer/students.html')

@app.route('/lecturer/records')
def lecturer_records_page():
    if 'lecturer_id' not in session: return redirect(url_for('lecturer_login'))
    return render_template('lecturer/records.html')

# ── Admin routes ──
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form['username']).first()
        if admin and bcrypt.check_password_hash(admin.password, request.form['password']):
            session['admin_id'] = admin.id
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid credentials')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session: return redirect(url_for('admin_login'))
    return render_template('admin_dashboard.html',
        faculties=Faculty.query.order_by(Faculty.name).all(),
        departments=Department.query.order_by(Department.name).all(),
        courses=Course.query.order_by(Course.code).all(),
        students=Student.query.all(),
        lecturers=Lecturer.query.all())

@app.route('/admin/faculty/add', methods=['POST'])
@db_transaction
def admin_add_faculty():
    if 'admin_id' not in session: return jsonify({'success': False})
    name = request.form.get('name', '').strip()
    if Faculty.query.filter_by(name=name).first():
        return jsonify({'success': False, 'message': 'Already exists'})
    db.session.add(Faculty(name=name))
    return jsonify({'success': True, 'message': 'Faculty added'})

@app.route('/admin/faculty/delete/<int:fid>', methods=['POST'])
@db_transaction
def admin_delete_faculty(fid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Faculty.query.get_or_404(fid))
    return jsonify({'success': True})

@app.route('/admin/department/add', methods=['POST'])
@db_transaction
def admin_add_department():
    if 'admin_id' not in session: return jsonify({'success': False})
    name = request.form.get('name', '').strip()
    faculty_id = int(request.form.get('faculty_id'))
    db.session.add(Department(name=name, faculty_id=faculty_id))
    return jsonify({'success': True, 'message': 'Department added'})

@app.route('/admin/department/delete/<int:did>', methods=['POST'])
@db_transaction
def admin_delete_department(did):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Department.query.get_or_404(did))
    return jsonify({'success': True})

@app.route('/admin/course/add', methods=['POST'])
@db_transaction
def admin_add_course():
    if 'admin_id' not in session: return jsonify({'success': False})
    code = request.form.get('code', '').strip().upper()
    title = request.form.get('title', '').strip()
    level = int(request.form.get('level'))
    semester = int(request.form.get('semester'))
    department_id = int(request.form.get('department_id'))
    db.session.add(Course(code=code, title=title, level=level, semester=semester, department_id=department_id))
    return jsonify({'success': True, 'message': 'Course added'})

@app.route('/admin/course/delete/<int:cid>', methods=['POST'])
@db_transaction
def admin_delete_course(cid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Course.query.get_or_404(cid))
    return jsonify({'success': True})

@app.route('/admin/reset_device/<int:student_id>', methods=['POST'])
@db_transaction
def admin_reset_device(student_id):
    if 'admin_id' not in session: return jsonify({'success': False})
    s = db.session.get(Student, student_id)
    if s:
        s.device_hash = None
        s.device_token = None
    return jsonify({'success': True, 'message': f'Device reset for {s.full_name}'})

@app.route('/admin/delete_student/<int:student_id>', methods=['POST'])
@db_transaction
def admin_delete_student(student_id):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Student.query.get_or_404(student_id))
    return jsonify({'success': True})

@app.route('/admin/delete_lecturer/<int:lid>', methods=['POST'])
@db_transaction
def admin_delete_lecturer(lid):
    if 'admin_id' not in session: return jsonify({'success': False})
    db.session.delete(Lecturer.query.get_or_404(lid))
    return jsonify({'success': True})

    # ── Admin: manage lecturer-course assignments ──
@app.route('/admin/lecturer_courses/<int:lecturer_id>')
def admin_lecturer_courses(lecturer_id):
    if 'admin_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    lecturer = db.session.get(Lecturer, lecturer_id)
    if not lecturer:
        return jsonify({'success': False, 'message': 'Lecturer not found'})
    assigned = [{'id': lc.course.id, 'code': lc.course.code, 'title': lc.course.title,
                 'level': lc.course.level, 'department': lc.course.department.name}
                for lc in lecturer.lecturer_courses]
    return jsonify({'success': True, 'courses': assigned})

@app.route('/admin/assign_lecturer_course', methods=['POST'])
@db_transaction
def admin_assign_lecturer_course():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    data = get_json_data()
    lecturer_id = data.get('lecturer_id')
    course_id = data.get('course_id')
    lecturer = db.session.get(Lecturer, lecturer_id)
    course = db.session.get(Course, course_id)
    if not lecturer or not course:
        return jsonify({'success': False, 'message': 'Invalid lecturer or course'})
    if LecturerCourse.query.filter_by(lecturer_id=lecturer_id, course_id=course_id).first():
        return jsonify({'success': False, 'message': 'Already assigned'})
    lc = LecturerCourse(lecturer_id=lecturer_id, course_id=course_id)
    db.session.add(lc)
    return jsonify({'success': True, 'message': f'{course.code} assigned to {lecturer.full_name}'})

@app.route('/admin/remove_lecturer_course', methods=['POST'])
@db_transaction
def admin_remove_lecturer_course():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    data = get_json_data()
    lecturer_id = data.get('lecturer_id')
    course_id = data.get('course_id')
    lc = LecturerCourse.query.filter_by(lecturer_id=lecturer_id, course_id=course_id).first()
    if not lc:
        return jsonify({'success': False, 'message': 'Not assigned'})
    db.session.delete(lc)
    return jsonify({'success': True, 'message': 'Course removed'})

# ── Admin: attendance report (CSV) ── you already have download_attendance,
# but we can add an endpoint to view stats as JSON for the admin dashboard.
@app.route('/admin/course_stats/<int:course_id>')
def admin_course_stats(course_id):
    if 'admin_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    course = db.session.get(Course, course_id)
    if not course:
        return jsonify({'success': False, 'message': 'Course not found'})
    sessions = ClassSession.query.filter_by(course_id=course_id).order_by(ClassSession.started_at.desc()).all()
    total_enrolled = course.enrolled_students.count()
    session_data = []
    for s in sessions:
        present = Attendance.query.filter_by(session_id=s.id, status='present').count()
        session_data.append({
            'id': s.id,
            'started_at': s.started_at.strftime('%d %b %Y %I:%M %p'),
            'pattern': s.pattern,
            'present': present,
            'absent': total_enrolled - present,
            'total': total_enrolled
        })
    return jsonify({'success': True, 'course_code': course.code, 'course_title': course.title,
                    'sessions': session_data, 'total_enrolled': total_enrolled})


# ---------- Global error handlers ----------
@app.errorhandler(400)
def bad_request(e):
    logger.warning(f"Bad request: {getattr(e, 'description', str(e))}")
    if request.is_json:
        return jsonify({'success': False, 'message': 'Bad request'}), 400
    return render_template('errors/400.html'), 400


@app.errorhandler(404)
def not_found(e):
    logger.info(f"Not found: {request.path}")
    if request.is_json:
        return jsonify({'success': False, 'message': 'Not found'}), 404
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    try:
        db.session.rollback()
    except Exception:
        pass
    logger.exception('Internal server error')
    if request.is_json:
        return jsonify({'success': False, 'message': 'Internal server error. Please try again later.'}), 500
    return render_template('errors/500.html'), 500

if __name__ == '__main__':
    # Use FLASK_DEBUG=1 in environment to enable debug mode locally
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug)