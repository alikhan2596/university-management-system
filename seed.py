import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
DB = ROOT / 'university.db'
SCHEMA = ROOT / 'schema.sql'

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
TEACHER_PASSWORD = os.getenv('TEACHER_PASSWORD')
STUDENT_PASSWORD = os.getenv('STUDENT_PASSWORD')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')

if not all([ADMIN_PASSWORD, TEACHER_PASSWORD, STUDENT_PASSWORD]):
    raise RuntimeError('ADMIN_PASSWORD, TEACHER_PASSWORD and STUDENT_PASSWORD must be set in .env')
if not SCHEMA.exists():
    raise RuntimeError('schema.sql must be in the same folder as seed.py')

random.seed(7)
conn = sqlite3.connect(DB)
conn.execute('PRAGMA foreign_keys=ON')
conn.executescript(SCHEMA.read_text(encoding='utf-8'))

# Reset demo data safely in child-to-parent order using fixed SQL.
conn.executescript("""
DELETE FROM audit_logs;
DELETE FROM assessment_results;
DELETE FROM assessments;
DELETE FROM attendance_records;
DELETE FROM attendance_sessions;
DELETE FROM enrollments;
DELETE FROM schedules;
DELETE FROM offerings;
DELETE FROM students;
DELETE FROM sections;
DELETE FROM teachers;
DELETE FROM courses;
DELETE FROM rooms;
DELETE FROM users;
DELETE FROM semesters;
DELETE FROM departments;
""")

# Departments
for code, name in [
    ('CS', 'Computer Science'),
    ('SE', 'Software Engineering'),
    ('DS', 'Data Science')
]:
    conn.execute('INSERT INTO departments(code,name) VALUES(?,?)', (code, name))

# Semester
cur = conn.execute(
    '''INSERT INTO semesters(name,academic_year,start_date,end_date,status)
       VALUES(?,?,?,?,?)''',
    ('Fall 2026', '2026-27', '2026-09-01', '2027-01-15', 'Active')
)
semester_id = cur.lastrowid

# Rooms
for i in range(1, 7):
    conn.execute(
        'INSERT INTO rooms(room_no,building,capacity,room_type) VALUES(?,?,?,?)',
        (f'CS-{100+i}', 'Main Block', 45, 'Classroom' if i < 5 else 'Lab')
    )

# Courses
course_seed = [
    ('DEV301', 'DevOps'), ('DBS201', 'Database Systems'),
    ('SWE330', 'Software Architecture'), ('CSE580', 'Information Security'),
    ('AI310', 'Artificial Intelligence'), ('WEB210', 'Web Engineering')
]
for code, name in course_seed:
    conn.execute(
        '''INSERT INTO courses(code,name,credit_hours,department_id,recommended_semester)
           VALUES(?,?,?,?,?)''',
        (code, name, 3, 1, 5)
    )

def create_user(username, email, password, role, display_name):
    cur = conn.execute(
        '''INSERT INTO users
           (username,email,password_hash,role,display_name,must_change_password)
           VALUES(?,?,?,?,?,1)''',
        (username, email, generate_password_hash(password), role, display_name)
    )
    return cur.lastrowid

# Admin
create_user(
    ADMIN_USERNAME,
    'admin@university.local',
    ADMIN_PASSWORD,
    'admin',
    'System Administrator'
)

# Teachers
for i in range(1, 7):
    user_id = create_user(
        f'teacher{i:02d}',
        f'teacher{i:02d}@university.local',
        TEACHER_PASSWORD,
        'teacher',
        f'Teacher {i:02d}'
    )
    conn.execute(
        '''INSERT INTO teachers
           (user_id,employee_no,department_id,designation,qualification,phone,joining_date)
           VALUES(?,?,?,?,?,?,?)''',
        (user_id, f'EMP-{i:03d}', 1, 'Lecturer', 'MS Computer Science',
         f'0300-000{i:04d}', '2024-01-15')
    )

# Sections
for i in range(1, 4):
    conn.execute(
        'INSERT INTO sections(name,department_id,semester_id,capacity) VALUES(?,?,?,?)',
        (f'BSCS-5{chr(64+i)}', 1, semester_id, 40)
    )

# Students and accounts
for i in range(1, 61):
    user_id = create_user(
        f'student{i:02d}',
        f'student{i:02d}@university.local',
        STUDENT_PASSWORD,
        'student',
        f'Student {i:02d}'
    )
    section_id = 1 + ((i - 1) % 3)
    conn.execute(
        '''INSERT INTO students
           (user_id,registration_no,department_id,section_id,status,phone,admission_date)
           VALUES(?,?,?,?,?,?,?)''',
        (user_id, f'2024-CS-{i:03d}', 1, section_id, 'Active',
         f'0311-100{i:04d}', '2024-09-01')
    )

teacher_ids = [r[0] for r in conn.execute('SELECT id FROM teachers ORDER BY id')]
course_ids = [r[0] for r in conn.execute('SELECT id FROM courses ORDER BY id')]
section_ids = [r[0] for r in conn.execute('SELECT id FROM sections ORDER BY id')]
room_ids = [r[0] for r in conn.execute('SELECT id FROM rooms ORDER BY id')]

# Course offerings and schedules
weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Monday']
for i, course_id in enumerate(course_ids):
    teacher_id = teacher_ids[i % len(teacher_ids)]
    section_id = section_ids[i % len(section_ids)]
    cur = conn.execute(
        '''INSERT INTO offerings
           (course_id,teacher_id,section_id,semester_id,max_students,status)
           VALUES(?,?,?,?,?,?)''',
        (course_id, teacher_id, section_id, semester_id, 40, 'Active')
    )
    offering_id = cur.lastrowid
    start_hour = 9 + (i % 4)
    conn.execute(
        '''INSERT INTO schedules
           (offering_id,room_id,day_of_week,start_time,end_time)
           VALUES(?,?,?,?,?)''',
        (offering_id, room_ids[i % len(room_ids)], weekdays[i],
         f'{start_hour:02d}:00', f'{start_hour+1:02d}:30')
    )

# Enroll each student into the offerings for the student's section.
students = conn.execute('SELECT id,section_id FROM students ORDER BY id').fetchall()
for student_id, section_id in students:
    offering_ids = [r[0] for r in conn.execute(
        'SELECT id FROM offerings WHERE section_id=? ORDER BY id', (section_id,)
    )]
    for offering_id in offering_ids:
        conn.execute(
            '''INSERT INTO enrollments(student_id,offering_id,enrollment_date,status)
               VALUES(?,?,?,?)''',
            (student_id, offering_id, '2026-09-01', 'Active')
        )

# Attendance and assessments/results
admin_user_id = conn.execute(
    'SELECT id FROM users WHERE role=? ORDER BY id LIMIT 1', ('admin',)
).fetchone()[0]

offering_ids = [r[0] for r in conn.execute('SELECT id FROM offerings ORDER BY id')]
for offering_id in offering_ids:
    enrolled_student_ids = [r[0] for r in conn.execute(
        'SELECT student_id FROM enrollments WHERE offering_id=? ORDER BY student_id',
        (offering_id,)
    )]

    for week in range(6):
        class_date = str(date(2026, 9, 7) + timedelta(days=week * 7))
        cur = conn.execute(
            '''INSERT INTO attendance_sessions
               (offering_id,class_date,topic,created_by)
               VALUES(?,?,?,?)''',
            (offering_id, class_date, f'Week {week+1}', admin_user_id)
        )
        session_id = cur.lastrowid
        for student_id in enrolled_student_ids:
            status = random.choices(
                ['Present', 'Absent', 'Late'], weights=[82, 12, 6], k=1
            )[0]
            conn.execute(
                '''INSERT INTO attendance_records(session_id,student_id,status)
                   VALUES(?,?,?)''',
                (session_id, student_id, status)
            )

    assessments = [
        ('Quiz 1', 'Quiz', 10, 10, '2026-09-25'),
        ('Midterm', 'Exam', 50, 30, '2026-10-20'),
        ('Assignment', 'Assignment', 20, 20, '2026-11-10')
    ]
    for name, kind, maximum, weight, assessment_date in assessments:
        cur = conn.execute(
            '''INSERT INTO assessments
               (offering_id,name,type,max_marks,weight,assessment_date)
               VALUES(?,?,?,?,?,?)''',
            (offering_id, name, kind, maximum, weight, assessment_date)
        )
        assessment_id = cur.lastrowid
        for student_id in enrolled_student_ids:
            marks = round(random.uniform(maximum * 0.35, maximum * 0.98), 1)
            conn.execute(
                '''INSERT INTO assessment_results
                   (assessment_id,student_id,marks,feedback)
                   VALUES(?,?,?,?)''',
                (assessment_id, student_id, marks, 'Generated demonstration data')
            )

conn.commit()

# Print verified row counts using fixed SQL statements.
print('Version 2 database seeded successfully.')
count_queries = {
    'users': 'SELECT COUNT(*) FROM users',
    'students': 'SELECT COUNT(*) FROM students',
    'teachers': 'SELECT COUNT(*) FROM teachers',
    'courses': 'SELECT COUNT(*) FROM courses',
    'sections': 'SELECT COUNT(*) FROM sections',
    'rooms': 'SELECT COUNT(*) FROM rooms',
    'offerings': 'SELECT COUNT(*) FROM offerings',
    'schedules': 'SELECT COUNT(*) FROM schedules',
    'enrollments': 'SELECT COUNT(*) FROM enrollments',
    'attendance_sessions': 'SELECT COUNT(*) FROM attendance_sessions',
    'attendance_records': 'SELECT COUNT(*) FROM attendance_records',
    'assessments': 'SELECT COUNT(*) FROM assessments',
    'assessment_results': 'SELECT COUNT(*) FROM assessment_results',
}
for table_name, query in count_queries.items():
    count = conn.execute(query).fetchone()[0]
    print(f'{table_name}: {count}')
print('All seeded users must change their temporary password at first login.')
conn.close()
