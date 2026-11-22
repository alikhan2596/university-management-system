from datetime import date
import sqlite3
from flask import render_template, request, redirect, url_for, flash, session, abort


def register_feature_routes(app, get_db_connection, login_required, roles_required, add_audit_log):

    def teacher_id_for_user(connection):
        row = connection.execute(
            'SELECT id FROM teachers WHERE user_id = ?', (session['uid'],)
        ).fetchone()
        return row['id'] if row else None

    def student_id_for_user(connection):
        row = connection.execute(
            'SELECT id FROM students WHERE user_id = ?', (session['uid'],)
        ).fetchone()
        return row['id'] if row else None

    # ---------------- ADMIN 360 PAGES ----------------
    @app.route('/admin/teachers')
    @roles_required('admin')
    def admin_teachers():
        c = get_db_connection()
        rows = c.execute('''
            SELECT t.id, t.employee_no, u.display_name, u.email, u.is_active,
                   d.code department, t.designation, t.qualification, t.phone,
                   COUNT(DISTINCT o.id) assigned_courses,
                   COUNT(DISTINCT e.student_id) assigned_students
            FROM teachers t
            JOIN users u ON u.id=t.user_id
            JOIN departments d ON d.id=t.department_id
            LEFT JOIN offerings o ON o.teacher_id=t.id
            LEFT JOIN enrollments e ON e.offering_id=o.id
            GROUP BY t.id ORDER BY u.display_name
        ''').fetchall()
        c.close()
        return render_template('admin_teachers.html', rows=rows)

    @app.route('/admin/students')
    @roles_required('admin')
    def admin_students():
        q = request.args.get('q', '').strip()
        c = get_db_connection()
        rows = c.execute('''
            SELECT s.id, s.registration_no, u.display_name, u.email, u.is_active,
                   d.code department, sec.name section, s.status, s.phone,
                   COUNT(DISTINCT e.offering_id) courses
            FROM students s
            JOIN users u ON u.id=s.user_id
            JOIN departments d ON d.id=s.department_id
            LEFT JOIN sections sec ON sec.id=s.section_id
            LEFT JOIN enrollments e ON e.student_id=s.id
            WHERE (?='' OR u.display_name LIKE ? OR s.registration_no LIKE ?)
            GROUP BY s.id ORDER BY s.registration_no
        ''', (q, f'%{q}%', f'%{q}%')).fetchall()
        c.close()
        return render_template('admin_students.html', rows=rows, q=q)

    @app.route('/admin/courses')
    @roles_required('admin')
    def admin_courses():
        c = get_db_connection()
        rows = c.execute('''
            SELECT c.id,c.code,c.name,c.credit_hours,d.code department,
                   c.recommended_semester,COUNT(DISTINCT o.id) offerings,
                   COUNT(DISTINCT e.student_id) students
            FROM courses c JOIN departments d ON d.id=c.department_id
            LEFT JOIN offerings o ON o.course_id=c.id
            LEFT JOIN enrollments e ON e.offering_id=o.id
            GROUP BY c.id ORDER BY c.code
        ''').fetchall()
        c.close()
        return render_template('admin_courses.html', rows=rows)

    @app.route('/admin/sections')
    @roles_required('admin')
    def admin_sections():
        c = get_db_connection()
        rows = c.execute('''
            SELECT s.id,s.name,d.code department,se.name semester,s.capacity,
                   COUNT(DISTINCT st.id) students,COUNT(DISTINCT o.id) offerings
            FROM sections s JOIN departments d ON d.id=s.department_id
            JOIN semesters se ON se.id=s.semester_id
            LEFT JOIN students st ON st.section_id=s.id
            LEFT JOIN offerings o ON o.section_id=s.id
            GROUP BY s.id ORDER BY s.name
        ''').fetchall()
        c.close()
        return render_template('admin_sections.html', rows=rows)

    # ---------------- TEACHER PAGES ----------------
    @app.route('/teacher/courses')
    @roles_required('teacher')
    def teacher_courses():
        c = get_db_connection(); tid = teacher_id_for_user(c)
        if not tid: c.close(); abort(403)
        rows = c.execute('''
            SELECT o.id,c.code,c.name,s.name section,se.name semester,
                   COUNT(DISTINCT e.student_id) students,
                   GROUP_CONCAT(DISTINCT sc.day_of_week||' '||sc.start_time||'-'||sc.end_time) schedule
            FROM offerings o JOIN courses c ON c.id=o.course_id
            JOIN sections s ON s.id=o.section_id JOIN semesters se ON se.id=o.semester_id
            LEFT JOIN enrollments e ON e.offering_id=o.id
            LEFT JOIN schedules sc ON sc.offering_id=o.id
            WHERE o.teacher_id=? GROUP BY o.id ORDER BY c.code
        ''', (tid,)).fetchall(); c.close()
        return render_template('teacher_courses.html', rows=rows)

    @app.route('/teacher/course/<int:offering_id>')
    @roles_required('teacher')
    def teacher_course_detail(offering_id):
        c=get_db_connection(); tid=teacher_id_for_user(c)
        course=c.execute('''SELECT o.id,c.code,c.name,s.name section,se.name semester
            FROM offerings o JOIN courses c ON c.id=o.course_id JOIN sections s ON s.id=o.section_id
            JOIN semesters se ON se.id=o.semester_id WHERE o.id=? AND o.teacher_id=?''',(offering_id,tid)).fetchone()
        if not course: c.close(); abort(403)
        students=c.execute('''SELECT st.id,st.registration_no,u.display_name,u.email,
            ROUND(100.0*SUM(CASE WHEN ar.status IN ('Present','Late') THEN 1 ELSE 0 END)/NULLIF(COUNT(ar.id),0),1) attendance
            FROM enrollments e JOIN students st ON st.id=e.student_id JOIN users u ON u.id=st.user_id
            LEFT JOIN attendance_records ar ON ar.student_id=st.id
            LEFT JOIN attendance_sessions ats ON ats.id=ar.session_id AND ats.offering_id=e.offering_id
            WHERE e.offering_id=? GROUP BY st.id ORDER BY st.registration_no''',(offering_id,)).fetchall()
        assessments=c.execute('''SELECT a.*,COUNT(r.id) entered FROM assessments a LEFT JOIN assessment_results r ON r.assessment_id=a.id
            WHERE a.offering_id=? GROUP BY a.id ORDER BY a.assessment_date''',(offering_id,)).fetchall();c.close()
        return render_template('teacher_course_detail.html',course=course,students=students,assessments=assessments)

    @app.route('/teacher/attendance/<int:offering_id>', methods=['GET','POST'])
    @roles_required('teacher')
    def teacher_attendance(offering_id):
        c=get_db_connection();tid=teacher_id_for_user(c)
        course=c.execute('''SELECT o.id,c.code,c.name,s.name section FROM offerings o JOIN courses c ON c.id=o.course_id
            JOIN sections s ON s.id=o.section_id WHERE o.id=? AND o.teacher_id=?''',(offering_id,tid)).fetchone()
        if not course:c.close();abort(403)
        students=c.execute('''SELECT st.id,st.registration_no,u.display_name FROM enrollments e JOIN students st ON st.id=e.student_id
            JOIN users u ON u.id=st.user_id WHERE e.offering_id=? ORDER BY st.registration_no''',(offering_id,)).fetchall()
        if request.method=='POST':
            class_date=request.form['class_date'];topic=request.form.get('topic','').strip()
            try:
                cur=c.execute('INSERT INTO attendance_sessions(offering_id,class_date,topic,created_by) VALUES(?,?,?,?)',(offering_id,class_date,topic,session['uid']))
                session_id=cur.lastrowid
                for st in students:
                    status=request.form.get(f'status_{st["id"]}','Absent')
                    c.execute('INSERT INTO attendance_records(session_id,student_id,status,remarks) VALUES(?,?,?,?)',(session_id,st['id'],status,''))
                c.commit();c.close();add_audit_log('CREATE','attendance_session',session_id,f'{course["code"]} {class_date}')
                flash('Attendance saved successfully.','success');return redirect(url_for('teacher_attendance',offering_id=offering_id))
            except sqlite3.IntegrityError:
                c.rollback();flash('Attendance session already exists for this date.','danger')
        sessions=c.execute('''SELECT ats.id,ats.class_date,ats.topic,COUNT(ar.id) total,
            SUM(CASE WHEN ar.status='Present' THEN 1 ELSE 0 END) present,
            SUM(CASE WHEN ar.status='Absent' THEN 1 ELSE 0 END) absent
            FROM attendance_sessions ats LEFT JOIN attendance_records ar ON ar.session_id=ats.id
            WHERE ats.offering_id=? GROUP BY ats.id ORDER BY ats.class_date DESC''',(offering_id,)).fetchall();c.close()
        return render_template('teacher_attendance.html',course=course,students=students,sessions=sessions,today=date.today().isoformat())

    @app.route('/teacher/assessments/<int:offering_id>', methods=['GET','POST'])
    @roles_required('teacher')
    def teacher_assessments(offering_id):
        c=get_db_connection();tid=teacher_id_for_user(c)
        course=c.execute('''SELECT o.id,c.code,c.name,s.name section FROM offerings o JOIN courses c ON c.id=o.course_id
            JOIN sections s ON s.id=o.section_id WHERE o.id=? AND o.teacher_id=?''',(offering_id,tid)).fetchone()
        if not course:c.close();abort(403)
        if request.method=='POST':
            try:
                cur=c.execute('INSERT INTO assessments(offering_id,name,type,max_marks,weight,assessment_date) VALUES(?,?,?,?,?,?)',
                    (offering_id,request.form['name'],request.form['type'],request.form['max_marks'],request.form['weight'],request.form['assessment_date']))
                c.commit();aid=cur.lastrowid;c.close();add_audit_log('CREATE','assessment',aid,course['code']);flash('Assessment created.','success');return redirect(url_for('teacher_assessments',offering_id=offering_id))
            except sqlite3.IntegrityError:flash('Invalid assessment data.','danger')
        rows=c.execute('''SELECT a.*,COUNT(r.id) result_count FROM assessments a LEFT JOIN assessment_results r ON r.assessment_id=a.id
            WHERE a.offering_id=? GROUP BY a.id ORDER BY a.assessment_date''',(offering_id,)).fetchall();c.close()
        return render_template('teacher_assessments.html',course=course,rows=rows)

    @app.route('/teacher/results/<int:assessment_id>', methods=['GET','POST'])
    @roles_required('teacher')
    def teacher_results(assessment_id):
        c=get_db_connection();tid=teacher_id_for_user(c)
        assessment=c.execute('''SELECT a.*,c.code,c.name course_name,s.name section,o.id offering_id FROM assessments a
            JOIN offerings o ON o.id=a.offering_id JOIN courses c ON c.id=o.course_id JOIN sections s ON s.id=o.section_id
            WHERE a.id=? AND o.teacher_id=?''',(assessment_id,tid)).fetchone()
        if not assessment:c.close();abort(403)
        students=c.execute('''SELECT st.id,st.registration_no,u.display_name,r.marks,r.feedback FROM enrollments e
            JOIN students st ON st.id=e.student_id JOIN users u ON u.id=st.user_id
            LEFT JOIN assessment_results r ON r.student_id=st.id AND r.assessment_id=?
            WHERE e.offering_id=? ORDER BY st.registration_no''',(assessment_id,assessment['offering_id'])).fetchall()
        if request.method=='POST':
            for st in students:
                raw=request.form.get(f'marks_{st["id"]}','').strip()
                if raw=='':continue
                marks=float(raw)
                if marks<0 or marks>assessment['max_marks']:
                    c.close();flash(f'Marks must be 0 to {assessment["max_marks"]}.','danger');return redirect(request.url)
                c.execute('''INSERT INTO assessment_results(assessment_id,student_id,marks,feedback) VALUES(?,?,?,?)
                    ON CONFLICT(assessment_id,student_id) DO UPDATE SET marks=excluded.marks,feedback=excluded.feedback''',
                    (assessment_id,st['id'],marks,request.form.get(f'feedback_{st["id"]}','')))
            c.commit();c.close();add_audit_log('UPDATE','assessment_results',assessment_id,assessment['code']);flash('Results saved.','success');return redirect(request.url)
        c.close();return render_template('teacher_results.html',assessment=assessment,students=students)

    @app.route('/teacher/timetable')
    @roles_required('teacher')
    def teacher_timetable():
        c=get_db_connection();tid=teacher_id_for_user(c)
        rows=c.execute('''SELECT sc.day_of_week,sc.start_time,sc.end_time,c.code,c.name,s.name section,r.room_no,r.building
            FROM schedules sc JOIN offerings o ON o.id=sc.offering_id JOIN courses c ON c.id=o.course_id
            JOIN sections s ON s.id=o.section_id JOIN rooms r ON r.id=sc.room_id WHERE o.teacher_id=?
            ORDER BY CASE sc.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END,sc.start_time''',(tid,)).fetchall();c.close()
        return render_template('timetable.html',title='My Timetable',rows=rows)

    # ---------------- STUDENT READ-ONLY PAGES ----------------
    @app.route('/student/courses')
    @roles_required('student')
    def student_courses():
        c=get_db_connection();sid=student_id_for_user(c)
        rows=c.execute('''SELECT o.id,c.code,c.name,c.credit_hours,u.display_name teacher,s.name section,se.name semester
            FROM enrollments e JOIN offerings o ON o.id=e.offering_id JOIN courses c ON c.id=o.course_id
            JOIN teachers t ON t.id=o.teacher_id JOIN users u ON u.id=t.user_id JOIN sections s ON s.id=o.section_id
            JOIN semesters se ON se.id=o.semester_id WHERE e.student_id=? ORDER BY c.code''',(sid,)).fetchall();c.close()
        return render_template('student_courses.html',rows=rows)

    @app.route('/student/attendance')
    @roles_required('student')
    def student_attendance():
        c=get_db_connection();sid=student_id_for_user(c)
        summary=c.execute('''SELECT c.code,c.name,COUNT(ar.id) sessions,
            ROUND(100.0*SUM(CASE WHEN ar.status IN ('Present','Late') THEN 1 ELSE 0 END)/NULLIF(COUNT(ar.id),0),1) percentage
            FROM enrollments e JOIN offerings o ON o.id=e.offering_id JOIN courses c ON c.id=o.course_id
            LEFT JOIN attendance_sessions ats ON ats.offering_id=o.id LEFT JOIN attendance_records ar ON ar.session_id=ats.id AND ar.student_id=e.student_id
            WHERE e.student_id=? GROUP BY o.id ORDER BY c.code''',(sid,)).fetchall()
        details=c.execute('''SELECT c.code,ats.class_date,ats.topic,ar.status FROM attendance_records ar
            JOIN attendance_sessions ats ON ats.id=ar.session_id JOIN offerings o ON o.id=ats.offering_id
            JOIN courses c ON c.id=o.course_id WHERE ar.student_id=? ORDER BY ats.class_date DESC''',(sid,)).fetchall();c.close()
        return render_template('student_attendance.html',summary=summary,details=details)

    @app.route('/student/results')
    @roles_required('student')
    def student_results():
        c=get_db_connection();sid=student_id_for_user(c)
        rows=c.execute('''SELECT c.code,c.name course_name,a.name assessment,a.type,a.max_marks,a.weight,r.marks,
            ROUND(100.0*r.marks/a.max_marks,1) percentage,r.feedback,a.assessment_date
            FROM assessment_results r JOIN assessments a ON a.id=r.assessment_id JOIN offerings o ON o.id=a.offering_id
            JOIN courses c ON c.id=o.course_id WHERE r.student_id=? ORDER BY a.assessment_date DESC''',(sid,)).fetchall();c.close()
        return render_template('student_results.html',rows=rows)

    @app.route('/student/timetable')
    @roles_required('student')
    def student_timetable():
        c=get_db_connection();sid=student_id_for_user(c)
        rows=c.execute('''SELECT sc.day_of_week,sc.start_time,sc.end_time,c.code,c.name,s.name section,r.room_no,r.building
            FROM enrollments e JOIN offerings o ON o.id=e.offering_id JOIN schedules sc ON sc.offering_id=o.id
            JOIN courses c ON c.id=o.course_id JOIN sections s ON s.id=o.section_id JOIN rooms r ON r.id=sc.room_id
            WHERE e.student_id=? ORDER BY CASE sc.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END,sc.start_time''',(sid,)).fetchall();c.close()
        return render_template('timetable.html',title='My Timetable',rows=rows)
