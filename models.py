from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# ── Faculty ──
class Faculty(db.Model):
    __tablename__ = 'faculties'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    departments = db.relationship('Department', backref='faculty', lazy=True, cascade='all, delete-orphan')

# ── Department ──
class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculties.id'), nullable=False)
    courses = db.relationship('Course', backref='department', lazy=True, cascade='all, delete-orphan')
    students = db.relationship('Student', backref='department_rel', lazy=True)
    lecturers = db.relationship('Lecturer', backref='department_rel', lazy=True)

# ── Course ── with indexes
class Course(db.Model):
    __tablename__ = 'courses'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, nullable=False)      # 100,200,300,400,500
    semester = db.Column(db.Integer, nullable=False)   # 1 or 2
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)
    lecturer_courses = db.relationship('LecturerCourse', backref='course', lazy=True, cascade='all, delete-orphan')
    sessions = db.relationship('ClassSession', backref='course', lazy=True)
    __table_args__ = (
        db.UniqueConstraint('code', 'department_id', name='uq_course_dept'),
        db.Index('ix_course_code', 'code'),
        db.Index('ix_course_level', 'level'),
    )

# ── LecturerCourse (many-to-many) ──
class LecturerCourse(db.Model):
    __tablename__ = 'lecturer_courses'
    id = db.Column(db.Integer, primary_key=True)
    lecturer_id = db.Column(db.Integer, db.ForeignKey('lecturers.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('lecturer_id', 'course_id', name='uq_lc'),)

# ── StudentCourse (NEW: many-to-many for student enrollment) ──
class StudentCourse(db.Model):
    __tablename__ = 'student_courses'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False)
    enrolled_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint('student_id', 'course_id', name='uq_student_course'),
        db.Index('ix_student_courses_student', 'student_id'),
        db.Index('ix_student_courses_course', 'course_id'),
    )

# ── Student ──
class Student(db.Model):
    __tablename__ = 'students'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    matric_no = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    parent_phone = db.Column(db.String(15), nullable=False)
    device_hash = db.Column(db.String(200), nullable=True)
    device_token = db.Column(db.String(128), nullable=True)   # new token for device binding
    assigned_pattern = db.Column(db.String(20), default='circle')
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculties.id'), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
    level = db.Column(db.Integer, nullable=True)
    reset_token = db.Column(db.String(64), nullable=True)   # increased length
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    attendances = db.relationship('Attendance', backref='student', lazy=True)
    faculty = db.relationship('Faculty', foreign_keys=[faculty_id])
    # NEW: enrolled courses
    enrolled_courses = db.relationship('Course', secondary='student_courses',
                                       backref=db.backref('enrolled_students', lazy='dynamic'))
    __table_args__ = (
        db.Index('ix_student_matric', 'matric_no'),
        db.Index('ix_student_email', 'email'),
    )

# ── Lecturer ──
class Lecturer(db.Model):
    __tablename__ = 'lecturers'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    reset_token = db.Column(db.String(64), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    lecturer_courses = db.relationship('LecturerCourse', backref='lecturer', lazy=True, cascade='all, delete-orphan')
    sessions = db.relationship('ClassSession', backref='lecturer', lazy=True)
    faculty = db.relationship('Faculty', secondary='departments',
                              primaryjoin='Lecturer.department_id==Department.id',
                              secondaryjoin='Department.faculty_id==Faculty.id',
                              viewonly=True, uselist=False)

# ── Session ↔ Department association ──
session_departments = db.Table('session_departments',
    db.Column('session_id', db.Integer, db.ForeignKey('class_sessions.id'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('departments.id'), primary_key=True)
)

# ── ClassSession ──
class ClassSession(db.Model):
    __tablename__ = 'class_sessions'
    id = db.Column(db.Integer, primary_key=True)
    lecturer_id = db.Column(db.Integer, db.ForeignKey('lecturers.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False)
    pattern = db.Column(db.String(20), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    attendances = db.relationship('Attendance', backref='session', lazy=True)
    departments = db.relationship('Department', secondary=session_departments,
                                  backref=db.backref('sessions', lazy=True))

# ── Attendance ──
class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id'), nullable=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=True)
    course_code = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(10), default='present')
    location_valid = db.Column(db.Boolean, default=False)
    device_valid = db.Column(db.Boolean, default=False)
    pattern_valid = db.Column(db.Boolean, default=False)
    course_rel = db.relationship('Course', foreign_keys=[course_id])
    __table_args__ = (
        db.UniqueConstraint('student_id', 'session_id', name='uq_attendance_student_session'),
    )

# ── Admin ──
class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)