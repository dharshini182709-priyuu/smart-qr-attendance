
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

COLLEGE_LAT = float(os.environ.get("COLLEGE_LAT", "11.067294"))
COLLEGE_LON = float(os.environ.get("COLLEGE_LON", "77.083478"))
ALLOWED_RADIUS = 100

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


def now_ist():
    return datetime.now(IST)


def db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )


# --------------------------------------------------
# DATABASE SETUP - PRESERVE EXISTING RECORDS
# --------------------------------------------------

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

            cur.execute("""
                DROP INDEX IF EXISTS idx_attendance_student_date
            """)

            cur.execute("""
                DROP INDEX IF EXISTS idx_attendance_device_date
            """)

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
# COLLEGE LOCATION VERIFICATION
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


# --------------------------------------------------
# ATTENDANCE SESSION - ALWAYS USE INDIA TIME
# --------------------------------------------------

def active_session():
    now = now_ist().strftime("%H:%M")

    if MORNING_START <= now <= MORNING_END:
        return "morning"

    if AFTERNOON_START <= now <= AFTERNOON_END:
        return "afternoon"

    return None


def current_date():
    return now_ist().date().isoformat()


def current_time():
    return now_ist().strftime("%I:%M:%S %p")


# --------------------------------------------------
# ADMIN AUTHENTICATION
# --------------------------------------------------

def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin_user"):
            return redirect(url_for("admin_login"))
        return function(*args, **kwargs)
    return wrapper


# --------------------------------------------------
# COMMON HTML LAYOUT
# --------------------------------------------------

BASE = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
body {
    font-family: Arial, sans-serif;
    background: #f3f5f8;
    margin: 0;
    padding: 20px;
    color: #222;
}
.card {
    max-width: 1100px;
    margin: 20px auto;
    background: white;
    padding: 24px;
    border-radius: 14px;
    box-shadow: 0 3px 14px #0001;
}
input, select, button {
    box-sizing: border-box;
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
.submit-btn {
    background: #176b38;
    width: 100%;
    margin-top: 12px;
    font-weight: bold;
    padding: 14px;
}
table {
    border-collapse: collapse;
    width: 100%;
    background: white;
    margin-top: 15px;
}
th, td {
    border: 1px solid #ddd;
    padding: 10px;
    text-align: left;
}
th {
    background: #222;
    color: white;
}
.msg {
    padding: 12px;
    background: #eef6ee;
    border-radius: 7px;
    margin: 12px 0;
}
.err {
    background: #ffe8e8;
    color: #9b111e;
}
.nav {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin: 15px 0;
}
label {
    display: inline-block;
    margin-top: 8px;
}
@media(max-width:650px) {
    body { padding: 8px; }
    .card {
        padding: 13px;
        margin: 8px auto;
        overflow-x: auto;
    }
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


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():
    conn = db()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT student_id, name
                FROM students
                ORDER BY student_id
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
    <tr>
        <td>{{ s.student_id }}</td>
        <td>{{ s.name }}</td>
    </tr>
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
            "<h1>Invalid QR Code</h1>"
            "<p>This QR code is not valid.</p>"
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

    if inside_college(
        data.get("latitude"), data.get("longitude")
    ):
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
# STUDENT ATTENDANCE - VISIBLE SUBMIT BUTTON
# --------------------------------------------------

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if not qr_required():
        return page(
            "Access Denied",
            "<h1>🔒 Access Denied</h1>"
            "<p>Scan the official QR and verify your college location first.</p>"
        )

    message = ""
    success = False

    if request.method == "POST":
        sid = (request.form.get("student_id") or "").strip()
        device = (request.form.get("device_id") or "").strip()

        if not sid:
            message = "Please enter your register number."

        elif len(device) < 20:
            message = (
                "Device verification is not ready. "
                "Refresh the page and try again."
            )

        else:
            sess = active_session()

            conn = db()

            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT student_id, name
                        FROM students
                        WHERE student_id = %s
                    """, (sid,))
                    student = cur.fetchone()

                    if not student:
                        message = "Register number not found."

                    elif not sess:
                        message = (
                            "Attendance time is closed. "
                            f"Morning: {MORNING_START}-{MORNING_END}; "
                            f"Afternoon: {AFTERNOON_START}-{AFTERNOON_END}. "
                            "Please check the allowed time and try again."
                        )

                    else:
                        today = current_date()
                        now = current_time()

                        cur.execute("""
                            SELECT id FROM attendance
                            WHERE student_id = %s
                              AND date = %s
                              AND session_name = %s
                        """, (sid, today, sess))

                        if cur.fetchone():
                            message = (
                                "This student has already marked "
                                "attendance for this session."
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
                                message = (
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
                                success = True

            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                message = (
                    "Attendance already exists for this student, "
                    "device, or session."
                )

            except Exception:
                conn.rollback()
                app.logger.exception("Student attendance submission failed")
                message = (
                    "Unable to submit attendance right now. "
                    "Please try again or contact the admin."
                )

            finally:
                conn.close()

            if success:
                body = """
                <h1>✅ Attendance Submitted Successfully</h1>
                <p>Name: <b>{{ name }}</b></p>
                <p>Register Number: <b>{{ sid }}</b></p>
                <p>Date: <b>{{ today }}</b></p>
                <p>Time: <b>{{ now }}</b></p>
                <p>Session: <b>{{ sess }}</b></p>
                <p>Your attendance has been saved.</p>
                <a class="btn" href="/">Home</a>
                """
                return page(
                    "Attendance Submitted",
                    body,
                    name=student["name"],
                    sid=sid,
                    today=today,
                    now=now,
                    sess=sess.title()
                )

    body = """
    <h1>📱 Student Attendance</h1>
    <p>Enter your register number and press Submit Attendance.</p>

    {% if message %}
    <p class="msg err">{{ message }}</p>
    {% endif %}

    <p><b>Morning:</b> {{ ms }}–{{ me }}</p>
    <p><b>Afternoon:</b> {{ astart }}–{{ aend }}</p>

    <form method="post" id="attendanceForm">
        <label for="student_id">Register Number</label><br>
        <input
            id="student_id"
            name="student_id"
            required
            autocomplete="off"
            placeholder="Enter your register number"
            style="width:100%;"
        >

        <input type="hidden" name="device_id" id="device_id">

        <button class="submit-btn" type="submit" id="submitBtn">
            Submit Attendance
        </button>
    </form>

    <p id="deviceMsg"></p>
    <a class="btn" href="/">Home</a>

    <script>
    (function () {
        const key = "smartqr_device_token";
        let token = "";

        try {
            token = localStorage.getItem(key) || "";

            if (!token || token.length < 20) {
                token = (
                    (window.crypto && crypto.randomUUID)
                    ? crypto.randomUUID() + crypto.randomUUID()
                    : "device-" + Date.now() + "-" +
                      Math.random().toString(36).slice(2) +
                      Math.random().toString(36).slice(2) +
                      Math.random().toString(36).slice(2)
                );

                localStorage.setItem(key, token);
            }

            document.getElementById("device_id").value = token;
        } catch (e) {
            document.getElementById("deviceMsg").textContent =
                "Device setup failed. Please refresh and try again.";
            document.getElementById("submitBtn").disabled = true;
        }

        document.getElementById("attendanceForm").addEventListener(
            "submit", function (event) {
                const reg = document.getElementById("student_id").value.trim();
                const device = document.getElementById("device_id").value;

                if (!reg) {
                    event.preventDefault();
                    alert("Please enter your register number.");
                    return;
                }

                if (!device || device.length < 20) {
                    event.preventDefault();
                    alert("Device verification is not ready. Refresh the page.");
                }
            }
        );
    })();
    </script>
    """

    return page(
        "Student Attendance",
        body,
        message=message,
        ms=MORNING_START,
        me=MORNING_END,
        astart=AFTERNOON_START,
        aend=AFTERNOON_END
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
    conn = db()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM students")
            total = cur.fetchone()["n"]

            cur.execute("""
                SELECT a.student_id,
                       COALESCE(s.name, 'Unknown') AS name,
                       a.date, a.time, a.session_name, a.status,
                       a.marked_by, a.reason
                FROM attendance a
                LEFT JOIN students s
                  ON s.student_id = a.student_id
                ORDER BY a.date DESC, a.time DESC
                LIMIT 300
            """)
            records = cur.fetchall()

            cur.execute("""
                SELECT student_id, name, parent_phone
                FROM students ORDER BY student_id
            """)
            students = cur.fetchall()

    finally:
        conn.close()

    body = """
    <h1>Admin Dashboard</h1>
    <p>Logged in as <b>{{ user }}</b></p>
    <p>Total Students: <b>{{ total }}</b></p>

    <div class="nav">
        <a class="btn" href="/admin/report">Attendance Percentage Report</a>
        <a class="btn" href="/admin/manual">Manual Attendance</a>
        <a class="btn" href="/admin/logout">Logout</a>
    </div>

    <h2>Student Database</h2>
    <table>
    <tr><th>Register No</th><th>Name</th><th>Parent Phone</th></tr>
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
        <td>{{ r.status }}</td>
        <td>{{ r.marked_by }}</td>
        <td>{{ r.reason or '—' }}</td>
    </tr>
    {% endfor %}
    </table>
    """

    return page(
        "Admin Dashboard",
        body,
        user=session["admin_user"],
        total=total,
        records=records,
        students=students
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
                        today = current_date()
                        now = current_time()

                        cur.execute("""
                            SELECT id FROM attendance
                            WHERE student_id = %s
                              AND date = %s
                              AND session_name = %s
                        """, (sid, today, sess))

                        if cur.fetchone():
                            error = "Attendance already exists for this session."

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

    return page(
        "Manual Attendance",
        body,
        message=message,
        error=error
    )


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
                raise ValueError("To Date must be on or after From Date.")

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
        <input type="date" name="from_date"
               value="{{ start }}" required><br>

        <label>To Date</label><br>
        <input type="date" name="to_date"
               value="{{ end }}" required><br>

        <label>Total Working Days</label><br>
        <input type="number" min="1" name="working_days"
               value="{{ working }}" required><br>

        <button type="submit">Calculate All Students</button>
    </form>

    {% if error %}
    <p class="msg err">{{ error }}</p>
    {% endif %}

    {% if report %}
    <h2>Report: {{ start }} to {{ end }}</h2>

    <table>
    <tr>
        <th>Register No</th>
        <th>Name</th>
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
        "Attendance Percentage Report",
        body,
        report=report,
        error=error,
        start=start,
        end=end,
        working=working
    )


@app.route("/attendance")
@admin_required
def attendance_redirect():
    return redirect(url_for("admin_dashboard"))


@app.route("/monthly")
def monthly_redirect():
    return redirect(url_for("admin_login"))


# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------

init_db()
import_students()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False
    )
