from sms_service import send_attendance_alert
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_bcrypt import Bcrypt
from models import db, Student, Lecturer, Attendance
import hashlib
import os

app = Flask(__name__)
app.secret_key = 'smart_attendance_secret_key'
bcrypt = Bcrypt(app)

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:123Qwerty?@localhost/smart_attendance'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ── Create all tables ──
with app.app_context():
    db.create_all()

# ── Home ──
@app.route('/')
def home():
    return redirect(url_for('login'))

# ── Student Login ──
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        matric_no = request.form['matric_no']
        password = request.form['password']
        device_hash = request.form.get('device_hash', '')
        student = Student.query.filter_by(matric_no=matric_no).first()

        if not student:
            return render_template('login.html', error='Invalid matric number or password')

        if not bcrypt.check_password_hash(student.password, password):
            return render_template('login.html', error='Invalid matric number or password')

        # Device check
        if student.device_hash and student.device_hash != device_hash:
            return render_template('login.html', error='This account is bound to a different device. Contact your lecturer.')

        session['student_id'] = student.id
        session['student_name'] = student.full_name
        return redirect(url_for('dashboard'))
    return render_template('login.html')

# ── Student Register ──
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        matric_no = request.form['matric_no']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        parent_phone = request.form['parent_phone']
        device_hash = request.form.get('device_hash', '')

        # Check if matric number already exists
        existing = Student.query.filter_by(matric_no=matric_no).first()
        if existing:
            return render_template('register.html', error='Matric number already exists')

        # Check if device is already bound to another account
        device_taken = Student.query.filter_by(device_hash=device_hash).first()
        if device_taken:
            return render_template('register.html', error='This device is already registered to another account. One device per student only.')

        student = Student(
            full_name=full_name,
            matric_no=matric_no,
            email=email,
            password=password,
            parent_phone=parent_phone,
            assigned_pattern='circle',
            device_hash=device_hash
        )
        db.session.add(student)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')
# ── Student Dashboard ──
@app.route('/dashboard')
def dashboard():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    attendances = Attendance.query.filter_by(student_id=student.id).order_by(Attendance.timestamp.desc()).all()
    return render_template('dashboard.html', student=student, attendances=attendances)

# ── Mark Attendance ──
@app.route('/mark_attendance', methods=['GET', 'POST'])
def mark_attendance():
    if 'student_id' not in session:
        return redirect(url_for('login'))
    student = Student.query.get(session['student_id'])
    if request.method == 'POST':
        data = request.get_json()
        pattern_valid = data.get('pattern_valid', False)
        location_valid = data.get('location_valid', False)
        device_hash = data.get('device_hash', '')
        course = data.get('course', 'General')

        # Device validation
        device_valid = False
        if student.device_hash is None:
            student.device_hash = device_hash
            db.session.commit()
            device_valid = True
        elif student.device_hash == device_hash:
            device_valid = True

        if pattern_valid and location_valid and device_valid:
            attendance = Attendance(
                student_id=student.id,
                course=course,
                status='present',
                location_valid=location_valid,
                device_valid=device_valid,
                pattern_valid=pattern_valid
            )
            db.session.add(attendance)
            db.session.commit()

            # Send SMS alert to parent
            try:
                send_attendance_alert(
                    student_name=student.full_name,
                    parent_phone=student.parent_phone,
                    course=course,
                    status='present',
                    timestamp=attendance.timestamp
                )
            except Exception as e:
                print(f"SMS error: {e}")

            return jsonify({'success': True, 'message': 'Attendance marked successfully! Parent notified via SMS.'})
        else:
            reasons = []
            if not pattern_valid:
                reasons.append('Invalid pattern')
            if not location_valid:
                reasons.append('Not within classroom location')
            if not device_valid:
                reasons.append('Unrecognized device')
            return jsonify({'success': False, 'message': ', '.join(reasons)})

    return render_template('mark_attendance.html', student=student)

# ── Logout ──
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Lecturer Login ──
@app.route('/lecturer/login', methods=['GET', 'POST'])
def lecturer_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        lecturer = Lecturer.query.filter_by(email=email).first()
        if lecturer and bcrypt.check_password_hash(lecturer.password, password):
            session['lecturer_id'] = lecturer.id
            session['lecturer_name'] = lecturer.full_name
            return redirect(url_for('lecturer_dashboard'))
        return render_template('lecturer_login.html', error='Invalid email or password')
    return render_template('lecturer_login.html')

# ── Lecturer Register ──
@app.route('/lecturer/register', methods=['GET', 'POST'])
def lecturer_register():
    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        course = request.form['course']
        existing = Lecturer.query.filter_by(email=email).first()
        if existing:
            return render_template('lecturer_register.html', error='Email already exists')
        lecturer = Lecturer(
            full_name=full_name,
            email=email,
            password=password,
            course=course
        )
        db.session.add(lecturer)
        db.session.commit()
        return redirect(url_for('lecturer_login'))
    return render_template('lecturer_register.html')

# ── Lecturer Dashboard ──
@app.route('/lecturer/dashboard')
def lecturer_dashboard():
    if 'lecturer_id' not in session:
        return redirect(url_for('lecturer_login'))
    lecturer = Lecturer.query.get(session['lecturer_id'])
    students = Student.query.all()
    attendances = Attendance.query.filter_by(course=lecturer.course).order_by(Attendance.timestamp.desc()).all()
    return render_template('lecturer_dashboard.html', lecturer=lecturer, students=students, attendances=attendances)

# ── Lecturer Sets Pattern & Location ──
@app.route('/lecturer/set_session', methods=['POST'])
def set_session():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    data = request.get_json()
    pattern = data.get('pattern')
    lat = data.get('lat')
    lng = data.get('lng')
    lecturer_id = session['lecturer_id']

    # Save to a simple session config (stored in app config for demo)
    app.config['ACTIVE_SESSION'] = {
        'pattern': pattern,
        'lat': lat,
        'lng': lng,
        'lecturer_id': lecturer_id
    }

    # Update all students' assigned pattern
    Student.query.update({'assigned_pattern': pattern})
    db.session.commit()

    return jsonify({'success': True, 'message': f'Session started! Pattern: {pattern}, Location set.'})

# ── Get Active Session (for student page) ──
@app.route('/get_session')
def get_session():
    active = app.config.get('ACTIVE_SESSION', None)
    if active:
        return jsonify({'success': True, 'pattern': active['pattern'], 'lat': active['lat'], 'lng': active['lng']})
    return jsonify({'success': False, 'message': 'No active session'})

# ── Lecturer Logout ──
@app.route('/lecturer/logout')
def lecturer_logout():
    session.clear()
    return redirect(url_for('lecturer_login'))

# ── Search Student by Matric No ──
@app.route('/lecturer/search_student')
def search_student():
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    matric_no = request.args.get('matric_no', '')
    student = Student.query.filter_by(matric_no=matric_no).first()
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    attendances = Attendance.query.filter_by(student_id=student.id).all()
    total = len(attendances)
    present = len([a for a in attendances if a.status == 'present'])
    absent = total - present
    rate = round((present / total * 100)) if total > 0 else 0

    # Group by session/date
    sessions = {}
    for a in attendances:
        date_key = a.timestamp.strftime('%d %b %Y')
        if date_key not in sessions:
            sessions[date_key] = {'present': 0, 'absent': 0, 'course': a.course}
        sessions[date_key][a.status] += 1

    return jsonify({
        'success': True,
        'student': {
            'name': student.full_name,
            'matric_no': student.matric_no,
            'email': student.email,
            'parent_phone': student.parent_phone,
            'assigned_pattern': student.assigned_pattern,
            'device_bound': student.device_hash is not None
        },
        'stats': {
            'total': total,
            'present': present,
            'absent': absent,
            'rate': rate
        },
        'sessions': sessions
    })

# ── Reset Student Device ──
@app.route('/lecturer/reset_device/<int:student_id>', methods=['POST'])
def reset_device(student_id):
    if 'lecturer_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    student = Student.query.get(student_id)
    if not student:
        return jsonify({'success': False, 'message': 'Student not found'})
    student.device_hash = None
    db.session.commit()
    return jsonify({'success': True, 'message': f'Device reset for {student.full_name}'})

# ── Get All Students with Stats ──
@app.route('/lecturer/all_students')
def all_students():
    if 'lecturer_id' not in session:
        return jsonify({'success': False})
    students = Student.query.all()
    result = []
    for s in students:
        attendances = Attendance.query.filter_by(student_id=s.id).all()
        total = len(attendances)
        present = len([a for a in attendances if a.status == 'present'])
        result.append({
            'id': s.id,
            'name': s.full_name,
            'matric_no': s.matric_no,
            'present': present,
            'absent': total - present,
            'total': total,
            'rate': round((present / total * 100)) if total > 0 else 0,
            'device_bound': s.device_hash is not None
        })
    return jsonify({'success': True, 'students': result})

if __name__ == '__main__':
    app.run(debug=True)