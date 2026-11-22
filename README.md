# University Management System Version 2

## Security
- Password hashes only
- Initial passwords provided through environment variables
- Forced password change on first login
- CSRF protection
- Role-based authorization
- Lockout for 15 minutes after 5 failed attempts
- 30-minute session timeout
- Parameterized SQL
- Audit log and generic error pages

## PythonAnywhere setup
1. Upload and extract this folder.
2. Open a Bash console and run `cd /home/YOUR_USERNAME/university_management_v2`.
3. Create a virtual environment and install dependencies if required: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and enter strong temporary passwords and a long random `SECRET_KEY`. Do not commit or share `.env`.
5. Run `python seed.py` once.
6. WSGI: add the project directory to `sys.path`, load dotenv from the project directory, then use `from app import app as application`.
7. Reload the web app and sign in using the usernames created by seed.py and the temporary passwords entered only in `.env`.
8. Every seeded user is forced to change the password at first login.

## Current implemented scope
Role-specific dashboards, departments, semesters, rooms, teacher-course-section assignments, conflict-aware timetable, secure authentication, password change and audit log. The relational schema also includes students, teachers, enrollment, attendance sessions/records, assessments and results for continued UI development.
