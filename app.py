
import os
import csv
import math
from datetime import datetime, date
from zoneinfo import ZoneInfo
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Flask, request, session, redirect,
    url_for, render_template_string, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY", "change-this-secret-key"
)

IST = ZoneInfo("Asia/Kolkata")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
CSV_FILE = "students.csv"
QR_TOKEN = os.environ.get("QR_TOKEN", "SMARTQR2026")

# Keep your correct college coordinates in Render Environment.
COLLEGE_LAT = float(os.environ.get("COLLEGE_LAT", "11.0679090"))
COLLEGE_LON = float(os.environ.get("COLLEGE_LON", "77.0833440"))
ALLOWED_RADIUS = 500

MORNING_START = os.environ.get("MORNING_START", "09:00")
MORNING_END = os.environ.get("MORNING_END", "09:30")
AFTERNOON_START = os.environ.get("AFTERNOON_START", "13:20")
AFTERNOON_END = os.environ.get("AFTERNOON_END", "13:45")

ADMINS = {
    os.environ.get("ADMIN1_USERNAME", "admin1"):
        os.environ.get("ADMIN1_PASSWORD", "ChangeMe123!"),
    os.environ.get("ADMIN2_USERNAME", "admin2"):
        os.environ.get("ADMIN2_PASSWORD", "ChangeMe456!")
}

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing. Configure Render PostgreSQL."
    )


# --------------------------------------------------
# INDIA TIME HELPERS
# --------------------------------------------------

def now_ist():
    return datetime.now(IST)


def today_ist():
    return now_ist().date().isoformat()


def time_ist():
    return now_ist().strftime("%I:%M:%S %p")


def active_session():
    current = now_ist().strftime("%H:%M")

    if MORNING_START <= current <= MORNING_END:
        return "morning"

    if AFTERNOON_START <= current <= AFTERNOON_END:
        return "afternoon"

    return None


def session_closed(session_name, selected_date):
    """Only show Absent after that session's attendance window closes."""
    today = now_ist().date()
    selected = date.fromisoformat(selected_date)

    if selected < today:
        return True

    if selected > today:
        return False

    current = now_ist().strftime("%H:%M")

    if session_name == "morning":
        return current > MORNING_END

    return current > AFTERNOON_END


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )


def init_db():
    conn = db()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_phone TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id BIGSERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    device_id TEXT,
                    session_name TEXT,
                    status TEXT DEFAULT 'Present',
                    marked_by TEXT DEFAULT 'QR',
                    reason TEXT
                )
            """)

            cur.execute("""
                ALTER TABLE students
                ADD COLUMN IF NOT EXISTS parent_phone TEXT
            """)

            cur.execute("""
                ALTER TABLE attendance
                ADD COLUMN IF NOT EXISTS session_name TEXT
            """)

            cur.execute("""
                ALTER TABLE attendance
                ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Present'
            """)

            cur.execute("""
                ALTER TABLE attendance
                ADD COLUMN IF NOT EXISTS marked_by TEXT DEFAULT 'QR'
            """)

            cur.execute("""
                ALTER TABLE attendance
                ADD COLUMN IF NOT EXISTS reason TEXT
            """)

            # Preserve existing historical attendance records.
            cur.execute("""
                UPDATE attendance
                SET session_name = 'legacy_full'
                WHERE session_name IS NULL OR session_name = ''
            """)

            cur.execute("""
                UPDATE attendance
                SET status = 'Present'
                WHERE status IS NULL OR status = ''
            """)

            cur.execute("""
                UPDATE attendance
                SET marked_by = 'QR'
                WHERE marked_by IS NULL OR marked_by = ''
            """)

            # Separate morning and afternoon entries.
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_attendance_student_session
                ON attendance(student_id, date, session_name)
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_attendance_device_session
                ON attendance(device_id, date, session_name)
                WHERE device_id IS NOT NULL
                  AND device_id <> ''
                  AND session_name <> 'legacy_full'
            """)

        conn.commit()

    finally:
        conn.close()


def import_students():
    if not os.path.exists(CSV_FILE):
        return

    conn = db()

    try:
        with conn.cursor() as cur:
            with open(
                CSV_FILE, "r", encoding="utf-8-sig", newline=""
            ) as file:
                reader = csv.DictReader(file)

                for row in reader:
                    sid = (
                        row.get("student_id")
                        or row.get("register_no")
                        or ""
                    ).strip()

                    name = (row.get("name") or "").strip()
                    phone = (
                        row.get("parent_phone") or ""
                    ).strip() or None

                    if sid and name:
                        cur.execute("""
                            INSERT INTO students
                                (student_id, name, parent_phone)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (student_id)
                            DO UPDATE SET
                                name = EXCLUDED.name,
                                parent_phone = COALESCE(
                                    EXCLUDED.parent_phone,
                                    students.parent_phone
                                )
                        """, (sid, name, phone))

        conn.commit()

    finally:
        conn.close()


# --------------------------------------------------
# LOCATION AND QR VALIDATION
# --------------------------------------------------

def inside_college(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return False

    radius = 6371000

    lat1 = math.radians(COLLEGE_LAT)
    lat2 = math.radians(latitude)
    dlat = math.radians(latitude - COLLEGE_LAT)
    dlon = math.radians(longitude - COLLEGE_LON)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    a = max(0, min(1, a))
    distance = 2 * radius * math.atan2(
        math.sqrt(a), math.sqrt(1 - a)
    )

    return distance <= ALLOWED_RADIUS


def qr_required():
    return (
        session.get("qr_verified", False)
        and session.get("location_verified", False)
    )


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin_user"):
            return redirect(url_for("admin_login"))
        return function(*args, **kwargs)
    return wrapper


# --------------------------------------------------
# COMMON HTML AND COLOURS
# --------------------------------------------------

BASE = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
* { box-sizing: border-box; }
body {
    font-family: Arial, sans-serif;
    background: #f3f5f8;
    margin: 0;
    padding: 16px;
    color: #222;
}
.card {
    max-width: 1200px;
    margin: 16px auto;
    background: white;
    padding: 24px;
    border-radius: 14px;
    box-shadow: 0 3px 14px #0001;
}
input, select, button {
    padding: 11px;
    margin: 6px 0;
    max-width: 100%;
    font-size: 15px;
}
input, select {
    border: 1px solid #bbb;
    border-radius: 6px;
}
button, .btn {
    background: #222;
    color: white;
    border: 0;
    border-radius: 7px;
    padding: 11px 16px;
    text-decoration: none;
    cursor: pointer;
    display: inline-block;
}
table {
    border-collapse: collapse;
    width: 100%;
    background: white;
    margin: 14px 0 22px;
}
th, td {
    border: 1px solid #ddd;
    padding: 10px;
    text-align: left;
}
th { background: #222; color: white; }
.nav {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin: 15px 0;
}
.msg {
    padding: 13px;
    background: #e8f7ed;
    color: #176b35;
    border-radius: 7px;
    margin: 12px 0;
    font-weight: bold;
}
.err {
    background: #ffe8e8;
    color: #a71919;
}
.present {
    color: #16803d;
    font-weight: bold;
    white-space: nowrap;
}
.absent {
    color: #c62828;
    font-weight: bold;
    white-space: nowrap;
}
.half {
    color: #b56b00;
    font-weight: bold;
}
.pending {
    color: #777;
    font-weight: bold;
}
.success-card {
    max-width: 650px;
    text-align: center;
    margin: 40px auto;
    border: 1px solid #b8e5c5;
    background: #f0fff4;
    padding: 28px;
    border-radius: 14px;
}
.success-icon {
    color: #16803d;
    font-size: 54px;
}
@media(max-width:700px) {
    body { padding: 7px; }
    .card { padding: 13px; margin: 7px auto; }
    table { font-size: 12px; }
    th, td { padding: 7px; }
}
</style>
</head>
<body>
<div class="card">{{ body|safe }}</div>
</body>
</html>
"""


def page(title, body, **context):
    rendered = render_template_string(body, **context)
    return render_template_string(BASE, title=title, body=rendered)


def status_html(present, closed):
    if present:
        return '<span class="present">✓ Present</span>'
    if closed:
        return '<span class="absent">✕ Absent</span>'
    return '<span class="pending">— Pending</span>'


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/")
def home():
    conn = db()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT student_id, name
                FROM students ORDER BY student_id
            """)
            students = cur.fetchall()
    finally:
        conn.close()

    body = """
    <h1>📱 Smart QR Attendance System</h1>
    <p>Total Students: <b>{{ students|length }}</b></p>

    <div class="nav">
        <a class="btn" href="/admin/login">Admin Login</a>
        <a class="btn" href="/scan">Student Attendance</a>
    </div>

    <h2>Student List</h2>
    <table>
    <tr><th>Register Number</th><th>Name</th></tr>
    {% for s in students %}
    <tr><td>{{ s.student_id }}</td><td>{{ s.name }}</td></tr>
    {% endfor %}
    </table>
    """
    return page("Smart QR Attendance", body, students=students)


# --------------------------------------------------
# QR ACCESS AND LOCATION CHECK
# --------------------------------------------------

@app.route("/access/<token>")
def qr_access(token):
    if token != QR_TOKEN:
        return page(
            "Invalid QR",
            "<h1>Invalid QR Code</h1><p>This QR code is not valid.</p>"
        )

    session.pop("qr_verified", None)
    session.pop("location_verified", None)

    body = """
    <h1>📍 Verify College Location</h1>
    <p>Please allow location access to mark attendance.</p>
    <p id="msg">Checking location...</p>

    <script>
    function showMessage(text) {
        document.getElementById("msg").textContent = text;
    }

    if (!navigator.geolocation) {
        showMessage("Location is not supported by this browser.");
    } else {
        navigator.geolocation.getCurrentPosition(async p => {
            try {
                const response = await fetch("/verify-location", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        latitude: p.coords.latitude,
                        longitude: p.coords.longitude
                    })
                });

                const data = await response.json();

                if (data.success) {
                    showMessage("Location verified. Opening attendance...");
                    setTimeout(() => location.href = "/scan", 500);
                } else {
                    showMessage(data.message);
                }
            } catch (e) {
                showMessage("Unable to verify location. Try again.");
            }
        }, () => {
            showMessage("Please allow location permission.");
        }, {enableHighAccuracy: true, timeout: 15000});
    }
    </script>
    """
    return page("Verify Location", body)


@app.route("/verify-location", methods=["POST"])
def verify_location():
    data = request.get_json(silent=True) or {}

    if inside_college(data.get("latitude"), data.get("longitude")):
        session["location_verified"] = True
        session["qr_verified"] = True
        return jsonify(success=True)

    session.pop("location_verified", None)
    session.pop("qr_verified", None)

    return jsonify(
        success=False,
        message="Attendance is allowed only within 100 metres of college."
    )


# --------------------------------------------------
# STUDENT ATTENDANCE
# --------------------------------------------------

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if not qr_required():
        return page(
            "Access Denied",
            "<h1>🔒 Access Denied</h1>"
            "<p>Scan the official QR and verify your location first.</p>"
        )

    message = ""
    error = ""

    if request.method == "POST":
        sid = (request.form.get("student_id") or "").strip()
        device = (request.form.get("device_id") or "").strip()
        sess = active_session()

        if not sid:
            error = "Enter your register number."
        elif not sess:
            error = (
                "Attendance time is closed. Morning: "
                f"{MORNING_START}-{MORNING_END}; Afternoon: "
                f"{AFTERNOON_START}-{AFTERNOON_END}."
            )
        elif len(device) < 20:
            error = "Device verification failed. Refresh and try again."
        else:
            conn = db()

            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT student_id, name
                        FROM students WHERE student_id = %s
                    """, (sid,))
                    student = cur.fetchone()

                    if not student:
                        error = "Register number not found."
                    else:
                        today = today_ist()
                        now = time_ist()

                        cur.execute("""
                            SELECT id FROM attendance
                            WHERE student_id = %s
                              AND date = %s
                              AND session_name = %s
                        """, (sid, today, sess))

                        if cur.fetchone():
                            error = (
                                "Attendance already marked for this session."
                            )
                        else:
                            cur.execute("""
                                SELECT student_id FROM attendance
                                WHERE device_id = %s
                                  AND date = %s
                                  AND session_name = %s
                                  AND session_name <> 'legacy_full'
                            """, (device, today, sess))

                            if cur.fetchone():
                                error = (
                                    "This device has already been used "
                                    "for this session."
                                )
                            else:
                                cur.execute("""
                                    INSERT INTO attendance
                                    (student_id, date, time, device_id,
                                     session_name, status, marked_by)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                                """, (
                                    sid, today, now, device,
                                    sess, "Present", "QR"
                                ))
                                conn.commit()

                                success_body = """
                                <div class="success-card">
                                    <div class="success-icon">✓</div>
                                    <h1 class="present">
                                        Attendance Marked Successfully!
                                    </h1>
                                    <p>Your attendance has been recorded.</p>
                                    <hr>
                                    <p><b>Name:</b> {{ name }}</p>
                                    <p><b>Register Number:</b> {{ sid }}</p>
                                    <p><b>Date:</b> {{ today }}</p>
                                    <p><b>Time:</b> {{ now }}</p>
                                    <p><b>Session:</b> {{ sess.title() }}</p>
                                    <a class="btn" href="/">Home</a>
                                </div>
                                """
                                return page(
                                    "Attendance Successful",
                                    success_body,
                                    name=student["name"],
                                    sid=sid,
                                    today=today,
                                    now=now,
                                    sess=sess
                                )

            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                error = "Attendance already exists for this session."

            finally:
                conn.close()

    body = """
    <h1>📱 Student Attendance</h1>

    {% if message %}
    <p class="msg">{{ message }}</p>
    {% endif %}
    {% if error %}
    <p class="msg err">{{ error }}</p>
    {% endif %}

    <p><b>Morning:</b> {{ ms }}–{{ me }}</p>
    <p><b>Afternoon:</b> {{ astart }}–{{ aend }}</p>

    <form method="post">
        <label>Register Number</label><br>
        <input name="student_id" required autocomplete="off"><br>
        <input type="hidden" name="device_id" id="device_id">
        <button type="submit">Submit Attendance</button>
    </form>

    <script>
    const key = "smartqr_device_token";
    let token = localStorage.getItem(key);

    if (!token) {
        token = (crypto.randomUUID)
            ? crypto.randomUUID()
            : "dev-" + Date.now() + "-" +
              Math.random().toString(36).slice(2) +
              Math.random().toString(36).slice(2);
        localStorage.setItem(key, token);
    }

    document.getElementById("device_id").value = token;
    </script>

    <a class="btn" href="/">Home</a>
    """

    return page(
        "Student Attendance", body,
        message=message, error=error,
        ms=MORNING_START, me=MORNING_END,
        astart=AFTERNOON_START, aend=AFTERNOON_END
    )


# --------------------------------------------------
# ADMIN LOGIN / LOGOUT
# --------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if username in ADMINS and password == ADMINS[username]:
            session.clear()
            session["admin_user"] = username
            return redirect(url_for("admin_dashboard"))

        error = "Invalid admin username or password."

    body = """
    <h1>🔐 Admin Login</h1>
    {% if error %}<p class="msg err">{{ error }}</p>{% endif %}

    <form method="post">
        <label>Username</label><br>
        <input name="username" required><br>
        <label>Password</label><br>
        <input name="password" type="password" required><br>
        <button type="submit">Login</button>
    </form>
    <a class="btn" href="/">Home</a>
    """
    return page("Admin Login", body, error=error)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    selected_date = request.args.get("date") or today_ist()

    try:
        date.fromisoformat(selected_date)
    except ValueError:
        selected_date = today_ist()

    conn = db()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM students")
            total = cur.fetchone()["n"]

            cur.execute("""
                SELECT student_id, name, parent_phone
                FROM students ORDER BY student_id
            """)
            students = cur.fetchall()

            cur.execute("""
                SELECT a.student_id,
                       COALESCE(s.name, 'Unknown') AS name,
                       a.date, a.time, a.session_name, a.status,
                       a.marked_by, a.reason
                FROM attendance a
                LEFT JOIN students s ON s.student_id = a.student_id
                ORDER BY a.date DESC, a.time DESC
                LIMIT 300
            """)
            records = cur.fetchall()

            cur.execute("""
                SELECT student_id, session_name, status
                FROM attendance
                WHERE date = %s
            """, (selected_date,))
            day_records = cur.fetchall()

    finally:
        conn.close()

    # Build each student's morning/afternoon status for selected date.
    by_student = {}

    for row in day_records:
        sid = row["student_id"]
        by_student.setdefault(sid, set())

        if row["status"] == "Present":
            kind = row["session_name"]

            if kind == "legacy_full":
                by_student[sid].update(["morning", "afternoon"])
            elif kind in ("morning", "afternoon"):
                by_student[sid].add(kind)

    morning_closed = session_closed("morning", selected_date)
    afternoon_closed = session_closed("afternoon", selected_date)

    daily_rows = []

    for student in students:
        sid = student["student_id"]
        present_sessions = by_student.get(sid, set())

        morning_present = "morning" in present_sessions
        afternoon_present = "afternoon" in present_sessions

        morning_html = status_html(morning_present, morning_closed)
        afternoon_html = status_html(
            afternoon_present, afternoon_closed
        )

        if morning_present and afternoon_present:
            final_status = (
                '<span class="present">✓ Full Day Present</span>'
            )
        elif morning_closed and afternoon_closed:
            if morning_present or afternoon_present:
                final_status = (
                    '<span class="half">◐ Half Day Present</span>'
                )
            else:
                final_status = '<span class="absent">✕ Absent</span>'
        else:
            final_status = '<span class="pending">In Progress</span>'

        daily_rows.append({
            "student_id": sid,
            "name": student["name"],
            "morning_html": morning_html,
            "afternoon_html": afternoon_html,
            "final_status": final_status
        })

    body = """
    <h1>Admin Dashboard</h1>
    <p>Logged in as <b>{{ user }}</b></p>
    <p>Total Students: <b>{{ total }}</b></p>

    <div class="nav">
        <a class="btn" href="/admin/report">Attendance Percentage Report</a>
        <a class="btn" href="/admin/manual">Manual Attendance</a>
        <a class="btn" href="/admin/logout">Logout</a>
    </div>

    <h2>Daily Attendance Status</h2>
    <form method="get">
        <label>Select Date</label><br>
        <input type="date" name="date" value="{{ selected_date }}" required>
        <button type="submit">View Attendance</button>
    </form>

    <table>
    <tr>
        <th>Register No</th>
        <th>Student Name</th>
        <th>Morning</th>
        <th>Afternoon</th>
        <th>Final Status</th>
    </tr>
    {% for r in daily_rows %}
    <tr>
        <td>{{ r.student_id }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.morning_html|safe }}</td>
        <td>{{ r.afternoon_html|safe }}</td>
        <td>{{ r.final_status|safe }}</td>
    </tr>
    {% endfor %}
    </table>

    <p>
        <span class="present">✓ Present</span> &nbsp;
        <span class="absent">✕ Absent</span> &nbsp;
        <span class="pending">— Pending</span>
    </p>

    <h2>Student Database</h2>
    <table>
    <tr>
        <th>Register No</th><th>Name</th><th>Parent Phone</th>
    </tr>
    {% for s in students %}
    <tr>
        <td>{{ s.student_id }}</td>
        <td>{{ s.name }}</td>
        <td>{{ s.parent_phone or '—' }}</td>
    </tr>
    {% endfor %}
    </table>

    <h2>Recent Attendance Records</h2>
    <table>
    <tr>
        <th>Register No</th><th>Name</th><th>Date</th><th>Time</th>
        <th>Session</th><th>Status</th><th>Marked By</th><th>Reason</th>
    </tr>
    {% for r in records %}
    <tr>
        <td>{{ r.student_id }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.date }}</td>
        <td>{{ r.time }}</td>
        <td>{{ r.session_name }}</td>
        <td>
        {% if r.status == 'Present' %}
            <span class="present">✓ Present</span>
        {% else %}
            <span class="absent">✕ {{ r.status }}</span>
        {% endif %}
        </td>
        <td>{{ r.marked_by }}</td>
        <td>{{ r.reason or '—' }}</td>
    </tr>
    {% endfor %}
    </table>
    """

    return page(
        "Admin Dashboard", body,
        user=session["admin_user"],
        total=total,
        students=students,
        records=records,
        daily_rows=daily_rows,
        selected_date=selected_date
    )


# --------------------------------------------------
# ADMIN MANUAL ATTENDANCE
# --------------------------------------------------

@app.route("/admin/manual", methods=["GET", "POST"])
@admin_required
def manual_attendance():
    message = ""
    error = ""

    if request.method == "POST":
        sid = (request.form.get("student_id") or "").strip()
        sess = (request.form.get("session_name") or "").strip().lower()
        reason = (request.form.get("reason") or "").strip()

        if sess not in ("morning", "afternoon") or not reason:
            error = "Select a session and enter a reason."
        else:
            conn = db()

            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT name FROM students WHERE student_id = %s
                    """, (sid,))
                    student = cur.fetchone()

                    if not student:
                        error = "Student register number not found."
                    else:
                        today = today_ist()
                        now = time_ist()

                        cur.execute("""
                            SELECT id FROM attendance
                            WHERE student_id = %s
                              AND date = %s
                              AND session_name = %s
                        """, (sid, today, sess))

                        if cur.fetchone():
                            error = (
                                "Attendance already exists for this session."
                            )
                        else:
                            cur.execute("""
                                INSERT INTO attendance
                                (student_id, date, time, device_id,
                                 session_name, status, marked_by, reason)
                                VALUES (%s, %s, %s, NULL, %s, %s, %s, %s)
                            """, (
                                sid, today, now, sess, "Present",
                                session["admin_user"], reason
                            ))

                            conn.commit()
                            message = (
                                f"Attendance marked for {student['name']} "
                                f"({sid}) — {sess.title()}."
                            )

            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                error = "Attendance already exists for this session."

            finally:
                conn.close()

    body = """
    <h1>Admin Manual Attendance</h1>
    <p>Admin location verification is not required. Reason is mandatory.</p>

    {% if message %}<p class="msg">{{ message }}</p>{% endif %}
    {% if error %}<p class="msg err">{{ error }}</p>{% endif %}

    <form method="post">
        <label>Register Number</label><br>
        <input name="student_id" required><br>

        <label>Session</label><br>
        <select name="session_name" required>
            <option value="">Select session</option>
            <option value="morning">Morning</option>
            <option value="afternoon">Afternoon</option>
        </select><br>

        <label>Reason</label><br>
        <input name="reason" required
               placeholder="QR issue / network issue"><br>

        <button type="submit">Mark Attendance</button>
    </form>

    <a class="btn" href="/admin">Back to Dashboard</a>
    """
    return page("Manual Attendance", body, message=message, error=error)


# --------------------------------------------------
# ATTENDANCE PERCENTAGE REPORT
# --------------------------------------------------

@app.route("/admin/report", methods=["GET", "POST"])
@admin_required
def attendance_report():
    report = []
    error = ""
    start = ""
    end = ""
    working = ""

    if request.method == "POST":
        start = request.form.get("from_date", "")
        end = request.form.get("to_date", "")
        working = request.form.get("working_days", "")

        try:
            date_from = date.fromisoformat(start)
            date_to = date.fromisoformat(end)
            working_days = int(working)

            if date_to < date_from:
                raise ValueError("Invalid date range.")

            if working_days <= 0:
                raise ValueError("Working days must be greater than zero.")

            conn = db()

            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT student_id, name
                        FROM students ORDER BY student_id
                    """)
                    students = cur.fetchall()

                    cur.execute("""
                        SELECT student_id, date, session_name, status
                        FROM attendance
                        WHERE date >= %s AND date <= %s
                        ORDER BY student_id, date
                    """, (start, end))
                    rows = cur.fetchall()

                counts = {
                    s["student_id"]: {
                        "morning": set(),
                        "afternoon": set(),
                        "legacy": set()
                    }
                    for s in students
                }

                for row in rows:
                    sid = row["student_id"]

                    if sid not in counts or row["status"] != "Present":
                        continue

                    day = row["date"]
                    kind = row["session_name"]

                    if kind == "legacy_full":
                        counts[sid]["legacy"].add(day)
                    elif kind == "morning":
                        counts[sid]["morning"].add(day)
                    elif kind == "afternoon":
                        counts[sid]["afternoon"].add(day)

                for student in students:
                    c = counts[student["student_id"]]

                    full_dates = (
                        c["legacy"]
                        | (c["morning"] & c["afternoon"])
                    )

                    half_dates = (
                        c["morning"] ^ c["afternoon"]
                    ) - full_dates

                    full = len(full_dates)
                    half = len(half_dates)
                    absent = max(0, working_days - full - half)

                    percentage = round(
                        ((full + half * 0.5) / working_days) * 100,
                        2
                    )

                    report.append({
                        "student_id": student["student_id"],
                        "name": student["name"],
                        "full": full,
                        "half": half,
                        "absent": absent,
                        "percentage": percentage
                    })

            finally:
                conn.close()

        except (ValueError, TypeError):
            error = "Enter valid dates and working days."

    body = """
    <h1>Attendance Percentage Report</h1>

    <form method="post">
        <label>From Date</label><br>
        <input type="date" name="from_date" value="{{ start }}" required><br>

        <label>To Date</label><br>
        <input type="date" name="to_date" value="{{ end }}" required><br>

        <label>Total Working Days</label><br>
        <input type="number" min="1" name="working_days"
               value="{{ working }}" required><br>

        <button type="submit">Calculate All Students</button>
    </form>

    {% if error %}<p class="msg err">{{ error }}</p>{% endif %}

    {% if report %}
    <h2>Report: {{ start }} to {{ end }}</h2>

    <table>
    <tr>
        <th>Register No</th>
        <th>Student Name</th>
        <th>Full Days</th>
        <th>Half Days</th>
        <th>Absent Days</th>
        <th>Percentage</th>
    </tr>
    {% for r in report %}
    <tr>
        <td>{{ r.student_id }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.full }}</td>
        <td>{{ r.half }}</td>
        <td>{{ r.absent }}</td>
        <td>{{ r.percentage }}%</td>
    </tr>
    {% endfor %}
    </table>
    {% endif %}

    <p>Full day = 1; half day = 0.5.</p>
    <p>
        Percentage = (Full Days + Half Days × 0.5)
        ÷ Total Working Days × 100.
    </p>

    <a class="btn" href="/admin">Back to Dashboard</a>
    """

    return page(
        "Attendance Percentage Report", body,
        report=report, error=error,
        start=start, end=end, working=working
    )


@app.route("/attendance")
@admin_required
def attendance_redirect():
    return redirect(url_for("admin_dashboard"))


@app.route("/monthly")
def monthly_redirect():
    return redirect(url_for("admin_login"))


# --------------------------------------------------
# STARTUP
# --------------------------------------------------

init_db()
import_students()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False
    )
