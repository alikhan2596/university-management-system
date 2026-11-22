import os
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATABASE = ROOT / "university.db"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(
        minutes=int(os.getenv("SESSION_MINUTES", "30"))
    ),
)

csrf = CSRFProtect(app)


def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def add_audit_log(action, entity, entity_id=None, details=""):
    connection = get_db_connection()
    connection.execute(
        """
        INSERT INTO audit_logs
        (user_id, action, entity, entity_id, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session.get("uid"), action, entity, entity_id, details),
    )
    connection.commit()
    connection.close()


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return function(*args, **kwargs)

    return wrapper


def roles_required(*allowed_roles):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            if not session.get("uid"):
                return redirect(url_for("login"))
            if session.get("role") not in allowed_roles:
                abort(403)
            return function(*args, **kwargs)

        return wrapper

    return decorator


def is_valid_password(password):
    return bool(
        len(password) >= 10
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password)
    )


@app.context_processor
def inject_context():
    return {"now": datetime.now()}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        connection = get_db_connection()
        user = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        locked = False
        if user and user["locked_until"]:
            try:
                locked = datetime.fromisoformat(user["locked_until"]) > datetime.now()
            except ValueError:
                locked = False

        if (
            user
            and user["is_active"]
            and not locked
            and check_password_hash(user["password_hash"], password)
        ):
            connection.execute(
                """
                UPDATE users
                SET failed_attempts = 0,
                    locked_until = NULL,
                    last_login = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), user["id"]),
            )
            connection.commit()
            connection.close()

            session.clear()
            session.permanent = True
            session.update(
                uid=user["id"],
                role=user["role"],
                name=user["display_name"],
                must_change=user["must_change_password"],
            )

            if user["must_change_password"]:
                return redirect(url_for("change_password"))
            return redirect(url_for("dashboard"))

        if user and not locked:
            failed_attempts = user["failed_attempts"] + 1
            locked_until = None

            if failed_attempts >= 5:
                locked_until = (datetime.now() + timedelta(minutes=15)).isoformat()

            connection.execute(
                """
                UPDATE users
                SET failed_attempts = ?, locked_until = ?
                WHERE id = ?
                """,
                (failed_attempts, locked_until, user["id"]),
            )
            connection.commit()

        connection.close()
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        connection = get_db_connection()
        user = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["uid"],),
        ).fetchone()

        if not user or not check_password_hash(
            user["password_hash"], current_password
        ):
            flash("Current password is incorrect.", "danger")
        elif not is_valid_password(new_password):
            flash(
                "Use 10+ characters with uppercase, lowercase, number and symbol.",
                "danger",
            )
        elif new_password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, must_change_password = 0
                WHERE id = ?
                """,
                (generate_password_hash(new_password), user["id"]),
            )
            connection.commit()
            connection.close()

            session["must_change"] = 0
            add_audit_log("CHANGE_PASSWORD", "user", session["uid"])
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard"))

        connection.close()

    return render_template("change_password.html")


@app.route("/")
@login_required
def dashboard():
    if session.get("must_change"):
        return redirect(url_for("change_password"))

    connection = get_db_connection()
    role = session["role"]
    data = {}

    if role == "admin":
        # Static SQL avoids dynamic table identifiers and keeps KPI values numeric.
        data["students"] = connection.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]
        data["teachers"] = connection.execute(
            "SELECT COUNT(*) FROM teachers"
        ).fetchone()[0]
        data["courses"] = connection.execute(
            "SELECT COUNT(*) FROM courses"
        ).fetchone()[0]
        data["offerings"] = connection.execute(
            "SELECT COUNT(*) FROM offerings"
        ).fetchone()[0]
        data["rooms"] = connection.execute(
            "SELECT COUNT(*) FROM rooms"
        ).fetchone()[0]

        data["pass_rate"] = connection.execute(
            """
            SELECT ROUND(
                100.0 * SUM(
                    CASE
                        WHEN 100.0 * result.marks / assessment.max_marks >= 50
                        THEN 1 ELSE 0
                    END
                ) / NULLIF(COUNT(*), 0),
                1
            )
            FROM assessment_results AS result
            JOIN assessments AS assessment
              ON assessment.id = result.assessment_id
            """
        ).fetchone()[0] or 0

        # Separate key fixes the sqlite3.Row list appearing inside Courses KPI.
        data["course_performance"] = connection.execute(
            """
            SELECT
                course.code,
                course.name,
                COUNT(DISTINCT enrollment.student_id) AS students,
                ROUND(
                    100.0 * SUM(
                        CASE
                            WHEN 100.0 * result.marks / assessment.max_marks >= 50
                            THEN 1 ELSE 0
                        END
                    ) / NULLIF(COUNT(result.id), 0),
                    1
                ) AS pass_rate
            FROM offerings AS offering
            JOIN courses AS course
              ON course.id = offering.course_id
            LEFT JOIN enrollments AS enrollment
              ON enrollment.offering_id = offering.id
            LEFT JOIN assessments AS assessment
              ON assessment.offering_id = offering.id
            LEFT JOIN assessment_results AS result
              ON result.assessment_id = assessment.id
             AND result.student_id = enrollment.student_id
            GROUP BY offering.id, course.code, course.name
            ORDER BY students DESC
            LIMIT 8
            """
        ).fetchall()

        data["workload"] = connection.execute(
            """
            SELECT
                user.display_name,
                COUNT(DISTINCT offering.id) AS courses,
                COUNT(DISTINCT offering.section_id) AS sections,
                COUNT(DISTINCT enrollment.student_id) AS students
            FROM teachers AS teacher
            JOIN users AS user
              ON user.id = teacher.user_id
            LEFT JOIN offerings AS offering
              ON offering.teacher_id = teacher.id
            LEFT JOIN enrollments AS enrollment
              ON enrollment.offering_id = offering.id
            GROUP BY teacher.id, user.display_name
            ORDER BY courses DESC, user.display_name
            LIMIT 8
            """
        ).fetchall()

    elif role == "teacher":
        teacher = connection.execute(
            "SELECT id FROM teachers WHERE user_id = ?",
            (session["uid"],),
        ).fetchone()

        if teacher is None:
            connection.close()
            abort(403)

        teacher_id = teacher["id"]

        data["courses"] = connection.execute(
            "SELECT COUNT(*) FROM offerings WHERE teacher_id = ?",
            (teacher_id,),
        ).fetchone()[0]

        data["students"] = connection.execute(
            """
            SELECT COUNT(DISTINCT enrollment.student_id)
            FROM enrollments AS enrollment
            JOIN offerings AS offering
              ON offering.id = enrollment.offering_id
            WHERE offering.teacher_id = ?
            """,
            (teacher_id,),
        ).fetchone()[0]

        data["today"] = connection.execute(
            """
            SELECT
                course.code,
                section.name AS section,
                schedule.start_time,
                schedule.end_time,
                room.room_no
            FROM schedules AS schedule
            JOIN offerings AS offering
              ON offering.id = schedule.offering_id
            JOIN courses AS course
              ON course.id = offering.course_id
            JOIN sections AS section
              ON section.id = offering.section_id
            JOIN rooms AS room
              ON room.id = schedule.room_id
            WHERE offering.teacher_id = ?
              AND schedule.day_of_week = ?
            ORDER BY schedule.start_time
            """,
            (teacher_id, datetime.now().strftime("%A")),
        ).fetchall()

        data["assigned"] = connection.execute(
            """
            SELECT
                offering.id,
                course.code,
                course.name,
                section.name AS section,
                COUNT(enrollment.id) AS students
            FROM offerings AS offering
            JOIN courses AS course
              ON course.id = offering.course_id
            JOIN sections AS section
              ON section.id = offering.section_id
            LEFT JOIN enrollments AS enrollment
              ON enrollment.offering_id = offering.id
            WHERE offering.teacher_id = ?
            GROUP BY offering.id, course.code, course.name, section.name
            """,
            (teacher_id,),
        ).fetchall()

    else:
        student = connection.execute(
            "SELECT id FROM students WHERE user_id = ?",
            (session["uid"],),
        ).fetchone()

        if student is None:
            connection.close()
            abort(403)

        student_id = student["id"]

        data["courses"] = connection.execute(
            "SELECT COUNT(*) FROM enrollments WHERE student_id = ?",
            (student_id,),
        ).fetchone()[0]

        data["attendance"] = connection.execute(
            """
            SELECT ROUND(
                100.0 * SUM(
                    CASE
                        WHEN status IN ('Present', 'Late') THEN 1 ELSE 0
                    END
                ) / NULLIF(COUNT(*), 0),
                1
            )
            FROM attendance_records
            WHERE student_id = ?
            """,
            (student_id,),
        ).fetchone()[0] or 0

        data["schedule"] = connection.execute(
            """
            SELECT
                course.code,
                course.name,
                schedule.day_of_week,
                schedule.start_time,
                schedule.end_time,
                room.room_no
            FROM enrollments AS enrollment
            JOIN offerings AS offering
              ON offering.id = enrollment.offering_id
            JOIN courses AS course
              ON course.id = offering.course_id
            JOIN schedules AS schedule
              ON schedule.offering_id = offering.id
            JOIN rooms AS room
              ON room.id = schedule.room_id
            WHERE enrollment.student_id = ?
            ORDER BY
                CASE schedule.day_of_week
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    ELSE 7
                END,
                schedule.start_time
            """,
            (student_id,),
        ).fetchall()

        data["results"] = connection.execute(
            """
            SELECT
                course.code,
                assessment.name,
                result.marks,
                assessment.max_marks,
                ROUND(100.0 * result.marks / assessment.max_marks, 1) AS pct
            FROM assessment_results AS result
            JOIN assessments AS assessment
              ON assessment.id = result.assessment_id
            JOIN offerings AS offering
              ON offering.id = assessment.offering_id
            JOIN courses AS course
              ON course.id = offering.course_id
            WHERE result.student_id = ?
            ORDER BY assessment.assessment_date DESC
            LIMIT 10
            """,
            (student_id,),
        ).fetchall()

    connection.close()
    return render_template("dashboard.html", data=data, role=role)


def register_admin_crud(name, table, fields):
    # These identifiers are developer-controlled constants, never request data.
    allowed_fields = {
        "departments": {"code", "name"},
        "semesters": {
            "name", "academic_year", "start_date", "end_date", "status"
        },
        "rooms": {"room_no", "building", "capacity", "room_type"},
    }
    if table not in allowed_fields:
        raise ValueError("Invalid database table")
    if not fields or not set(fields).issubset(allowed_fields[table]):
        raise ValueError("Invalid database fields")

    @app.route(f"/{name}", endpoint=name)
    @roles_required("admin")
    def listing():
        connection = get_db_connection()
        rows = connection.execute(
            f"SELECT * FROM {table} ORDER BY id DESC"  # nosec B608 - validated allowlist
        ).fetchall()
        connection.close()
        return render_template(
            "crud_list.html",
            title=name.title(),
            rows=rows,
            fields=fields,
            name=name,
        )

    @app.route(f"/{name}/add", methods=["GET", "POST"], endpoint=f"add_{name}")
    @roles_required("admin")
    def add():
        if request.method == "POST":
            values = [request.form.get(field, "").strip() for field in fields]
            placeholders = ",".join(["?"] * len(fields))
            connection = get_db_connection()

            try:
                cursor = connection.execute(
                    f"INSERT INTO {table} ({','.join(fields)}) VALUES ({placeholders})",  # nosec B608 - validated allowlist
                    values,
                )
                connection.commit()
                entity_id = cursor.lastrowid
                connection.close()
                add_audit_log("CREATE", table, entity_id)
                flash("Record created successfully.", "success")
            except sqlite3.IntegrityError:
                connection.close()
                flash("Duplicate or invalid data.", "danger")

            return redirect(url_for(name))

        return render_template(
            "crud_form.html",
            title="Add " + name[:-1].title(),
            fields=fields,
        )


register_admin_crud("departments", "departments", ["code", "name"])
register_admin_crud(
    "semesters",
    "semesters",
    ["name", "academic_year", "start_date", "end_date", "status"],
)
register_admin_crud(
    "rooms",
    "rooms",
    ["room_no", "building", "capacity", "room_type"],
)


@app.route("/offerings", methods=["GET", "POST"])
@roles_required("admin")
def offerings():
    connection = get_db_connection()

    if request.method == "POST":
        try:
            cursor = connection.execute(
                """
                INSERT INTO offerings
                (course_id, teacher_id, section_id, semester_id, max_students, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    request.form[key]
                    for key in [
                        "course_id",
                        "teacher_id",
                        "section_id",
                        "semester_id",
                        "max_students",
                        "status",
                    ]
                ),
            )
            connection.commit()
            entity_id = cursor.lastrowid
            connection.close()
            add_audit_log("CREATE", "offerings", entity_id)
            flash("Course assigned successfully.", "success")
            return redirect(url_for("offerings"))
        except sqlite3.IntegrityError:
            flash("Offering already exists or contains invalid data.", "danger")

    rows = connection.execute(
        """
        SELECT
            offering.id,
            course.code,
            course.name,
            user.display_name AS teacher,
            section.name AS section,
            semester.name AS semester,
            offering.max_students,
            offering.status
        FROM offerings AS offering
        JOIN courses AS course ON course.id = offering.course_id
        JOIN teachers AS teacher ON teacher.id = offering.teacher_id
        JOIN users AS user ON user.id = teacher.user_id
        JOIN sections AS section ON section.id = offering.section_id
        JOIN semesters AS semester ON semester.id = offering.semester_id
        ORDER BY offering.id DESC
        """
    ).fetchall()

    options = {
        "courses": connection.execute(
            "SELECT id, code || ' - ' || name AS label FROM courses ORDER BY code"
        ).fetchall(),
        "teachers": connection.execute(
            """
            SELECT teacher.id, user.display_name AS label
            FROM teachers AS teacher
            JOIN users AS user ON user.id = teacher.user_id
            ORDER BY user.display_name
            """
        ).fetchall(),
        "sections": connection.execute(
            "SELECT id, name AS label FROM sections ORDER BY name"
        ).fetchall(),
        "semesters": connection.execute(
            "SELECT id, name AS label FROM semesters ORDER BY start_date DESC"
        ).fetchall(),
    }

    connection.close()
    return render_template("offerings.html", rows=rows, opts=options)


@app.route("/schedules", methods=["GET", "POST"])
@roles_required("admin")
def schedules():
    connection = get_db_connection()

    if request.method == "POST":
        offering_id = request.form["offering_id"]
        room_id = request.form["room_id"]
        day = request.form["day_of_week"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]

        clash = connection.execute(
            """
            SELECT 1
            FROM schedules AS schedule
            JOIN offerings AS existing_offering
              ON existing_offering.id = schedule.offering_id
            JOIN offerings AS new_offering
              ON new_offering.id = ?
            WHERE schedule.day_of_week = ?
              AND NOT (
                    schedule.end_time <= ?
                    OR schedule.start_time >= ?
              )
              AND (
                    schedule.room_id = ?
                    OR existing_offering.teacher_id = new_offering.teacher_id
                    OR existing_offering.section_id = new_offering.section_id
              )
            LIMIT 1
            """,
            (offering_id, day, start_time, end_time, room_id),
        ).fetchone()

        if not start_time or not end_time:
            flash("Start and end time are required.", "danger")
        elif start_time >= end_time:
            flash("End time must be after start time.", "danger")
        elif clash:
            flash(
                "Schedule conflict: room, teacher, or section is already booked.",
                "danger",
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO schedules
                (offering_id, room_id, day_of_week, start_time, end_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (offering_id, room_id, day, start_time, end_time),
            )
            connection.commit()
            entity_id = cursor.lastrowid
            connection.close()
            add_audit_log("CREATE", "schedules", entity_id)
            flash("Schedule created successfully.", "success")
            return redirect(url_for("schedules"))

    rows = connection.execute(
        """
        SELECT
            schedule.id,
            course.code,
            section.name AS section,
            user.display_name AS teacher,
            room.room_no,
            schedule.day_of_week,
            schedule.start_time,
            schedule.end_time
        FROM schedules AS schedule
        JOIN offerings AS offering ON offering.id = schedule.offering_id
        JOIN courses AS course ON course.id = offering.course_id
        JOIN sections AS section ON section.id = offering.section_id
        JOIN teachers AS teacher ON teacher.id = offering.teacher_id
        JOIN users AS user ON user.id = teacher.user_id
        JOIN rooms AS room ON room.id = schedule.room_id
        ORDER BY
            CASE schedule.day_of_week
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
                ELSE 7
            END,
            schedule.start_time
        """
    ).fetchall()

    offering_options = connection.execute(
        """
        SELECT offering.id, course.code || ' / ' || section.name AS label
        FROM offerings AS offering
        JOIN courses AS course ON course.id = offering.course_id
        JOIN sections AS section ON section.id = offering.section_id
        ORDER BY course.code, section.name
        """
    ).fetchall()

    room_options = connection.execute(
        "SELECT id, room_no || ' - ' || building AS label FROM rooms ORDER BY room_no"
    ).fetchall()

    connection.close()
    return render_template(
        "schedules.html",
        rows=rows,
        offerings=offering_options,
        rooms=room_options,
    )

from feature_routes import register_feature_routes

register_feature_routes(
    app,
    get_db_connection,
    login_required,
    roles_required,
    add_audit_log
)

@app.route("/audit")
@roles_required("admin")
def audit_page():
    connection = get_db_connection()
    rows = connection.execute(
        """
        SELECT audit.*, user.display_name
        FROM audit_logs AS audit
        LEFT JOIN users AS user ON user.id = audit.user_id
        ORDER BY audit.id DESC
        LIMIT 200
        """
    ).fetchall()
    connection.close()
    return render_template("audit.html", rows=rows)


@app.errorhandler(403)
def error_403(error):
    return (
        render_template(
            "error.html",
            code=403,
            message="You are not authorized to access this page.",
        ),
        403,
    )


@app.errorhandler(404)
def error_404(error):
    return (
        render_template(
            "error.html",
            code=404,
            message="The requested page was not found.",
        ),
        404,
    )


@app.errorhandler(500)
def error_500(error):
    return (
        render_template(
            "error.html",
            code=500,
            message="An internal error occurred.",
        ),
        500,
    )


if __name__ == "__main__":
    app.run()
