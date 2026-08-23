import csv
import sqlite3
import secrets

from datetime import datetime
from flask import Flask, render_template_string, request, session, redirect

from database import (
    create_tables,
    get_students,
    get_attendance
)


app = Flask(__name__)

# --------------------------------
# SECURITY
# --------------------------------

app.secret_key = secrets.token_hex(32)

# This is the secret QR access token.
# The QR code must open this URL:
#
# http://YOUR-IP:5000/access/SMARTQR2026
#
QR_TOKEN = "SMARTQR2026"


DB_NAME = "attendance.db"
CSV_FILE = "students.csv"


# --------------------------------
# DATABASE CONNECTION
# --------------------------------

def connect_db():
    return sqlite3.connect(DB_NAME)


# --------------------------------
# IMPORT STUDENTS FROM CSV
# --------------------------------

def import_students():

    print("Reading students.csv...")

    try:

        conn = connect_db()
        cursor = conn.cursor()

        with open(
            CSV_FILE,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            print("CSV columns:", reader.fieldnames)

            count = 0

            for row in reader:

                student_id = str(
                    row["student_id"]
                ).strip()

                name = str(
                    row["name"]
                ).strip()

                if student_id and name:

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO students
                        (student_id, name)
                        VALUES (?, ?)
                        """,
                        (student_id, name)
                    )

                    count += 1

        conn.commit()
        conn.close()

        print(
            f"{count} students imported successfully!"
        )

    except Exception as e:

        print("ERROR:", e)


# --------------------------------
# QR ACCESS ROUTE
# --------------------------------

@app.route("/access/<token>")
def qr_access(token):

    if token != QR_TOKEN:

        return """
        <html>
        <body style="
            font-family:Arial;
            text-align:center;
            margin-top:100px;
        ">

        <h1 style="color:red;">
            ❌ Invalid QR Code
        </h1>

        <p>
            This QR code is not valid.
        </p>

        </body>
        </html>
        """

    # QR successfully scanned
    session["qr_verified"] = True

    return redirect("/scan")


# --------------------------------
# SECURITY CHECK
# --------------------------------

def qr_required():

    return session.get(
        "qr_verified",
        False
    )


# --------------------------------
# HOME PAGE
# --------------------------------

@app.route("/")
def home():

    students = get_students()

    html = """
    <!DOCTYPE html>

    <html>

    <head>

        <title>Smart QR Attendance</title>

        <style>

            body {
                font-family: Arial;
                background: #f4f6f8;
                margin: 40px;
            }

            h1 {
                color: #222;
            }

            .box {
                background: white;
                padding: 25px;
                margin-bottom: 25px;
                border-radius: 12px;
            }

            .button-container {
                display: flex;
                gap: 15px;
                flex-wrap: wrap;
                margin-top: 20px;
            }

            .button {
                display: inline-block;
                padding: 13px 22px;
                background: #222;
                color: white;
                text-decoration: none;
                border-radius: 7px;
                font-size: 16px;
            }

            .monthly {
                background: #444;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                background: white;
            }

            th {
                background: #222;
                color: white;
            }

            th, td {
                padding: 12px;
                border: 1px solid #ddd;
                text-align: left;
            }

        </style>

    </head>

    <body>

        <h1>
            📱 Smart QR Attendance System
        </h1>

        <div class="box">

            <h2>
                Total Students: {{ total }}
            </h2>

            <div class="button-container">

                <a
                    href="/scan"
                    class="button"
                >
                    📱 Open Attendance
                </a>

                <a
                    href="/attendance"
                    class="button"
                >
                    📊 View Attendance
                </a>

                <a
                    href="/monthly"
                    class="button monthly"
                >
                    📈 Monthly Percentage
                </a>

            </div>

        </div>


        <table>

            <tr>
                <th>Register Number</th>
                <th>Name</th>
            </tr>

            {% for student in students %}

            <tr>

                <td>
                    {{ student[0] }}
                </td>

                <td>
                    {{ student[1] }}
                </td>

            </tr>

            {% endfor %}

        </table>

    </body>

    </html>
    """

    return render_template_string(
        html,
        students=students,
        total=len(students)
    )


# --------------------------------
# ATTENDANCE PAGE
# --------------------------------

@app.route(
    "/scan",
    methods=["GET", "POST"]
)
def scan():

    # --------------------------------
    # QR SECURITY
    # --------------------------------

    if not qr_required():

        return render_template_string(
            """
            <!DOCTYPE html>

            <html>

            <head>

                <title>Access Denied</title>

                <style>

                    body {
                        font-family: Arial;
                        background: #f4f6f8;
                        text-align: center;
                        padding-top: 100px;
                    }

                    .box {
                        background: white;
                        max-width: 500px;
                        margin: auto;
                        padding: 35px;
                        border-radius: 15px;
                    }

                    h1 {
                        color: red;
                    }

                </style>

            </head>

            <body>

                <div class="box">

                    <h1>
                        🔒 Access Denied
                    </h1>

                    <p>
                        Please scan the official
                        Attendance QR Code to continue.
                    </p>

                </div>

            </body>

            </html>
            """
        )


    # --------------------------------
    # POST
    # --------------------------------

    if request.method == "POST":

        student_id = request.form.get(
            "student_id",
            ""
        ).strip()


        # --------------------------------
        # CONFIRM ATTENDANCE
        # --------------------------------

        if request.form.get("confirm") == "yes":

            conn = connect_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT student_id, name
                FROM students
                WHERE student_id = ?
                """,
                (student_id,)
            )

            student = cursor.fetchone()


            if not student:

                conn.close()

                return render_template_string(
                    """
                    <html>

                    <body style="
                        font-family:Arial;
                        text-align:center;
                        margin-top:80px;
                    ">

                    <h2 style="color:red;">
                        ❌ Student not found
                    </h2>

                    <a href="/scan">
                        Try Again
                    </a>

                    </body>

                    </html>
                    """
                )


            now = datetime.now()

            date = now.strftime(
                "%Y-%m-%d"
            )

            time = now.strftime(
                "%H:%M:%S"
            )


            # --------------------------------
            # CHECK DUPLICATE
            # --------------------------------

            cursor.execute(
                """
                SELECT id
                FROM attendance
                WHERE student_id = ?
                AND date = ?
                """,
                (
                    student[0],
                    date
                )
            )

            already_marked = cursor.fetchone()


            if already_marked:

                conn.close()

                return render_template_string(
                    """
                    <!DOCTYPE html>

                    <html>

                    <head>

                        <title>Already Marked</title>

                        <style>

                            body {
                                font-family: Arial;
                                background: #f4f6f8;
                                text-align: center;
                                padding-top: 70px;
                            }

                            .box {
                                background: white;
                                max-width: 500px;
                                margin: auto;
                                padding: 35px;
                                border-radius: 15px;
                            }

                            h1 {
                                color: orange;
                            }

                            .button {
                                display: inline-block;
                                margin-top: 20px;
                                padding: 12px 25px;
                                background: #222;
                                color: white;
                                text-decoration: none;
                                border-radius: 6px;
                            }

                        </style>

                    </head>

                    <body>

                        <div class="box">

                            <h1>
                                ⚠️ Already Marked!
                            </h1>

                            <h2>
                                {{ name }}
                            </h2>

                            <p>
                                Register Number:
                                <b>{{ student_id }}</b>
                            </p>

                            <p>
                                Attendance for today
                                has already been submitted.
                            </p>

                            <a
                                class="button"
                                href="/scan"
                            >
                                ← Back
                            </a>

                        </div>

                    </body>

                    </html>
                    """,
                    name=student[1],
                    student_id=student[0]
                )


            # --------------------------------
            # SAVE ATTENDANCE
            # --------------------------------

            cursor.execute(
                """
                INSERT INTO attendance
                (student_id, date, time)
                VALUES (?, ?, ?)
                """,
                (
                    student[0],
                    date,
                    time
                )
            )

            conn.commit()
            conn.close()


            # --------------------------------
            # SUCCESS
            # --------------------------------

            return render_template_string(
                """
                <!DOCTYPE html>

                <html>

                <head>

                    <title>Attendance Submitted</title>

                    <style>

                        body {
                            font-family: Arial;
                            background: #f4f6f8;
                            text-align: center;
                            padding-top: 70px;
                        }

                        .box {
                            background: white;
                            max-width: 500px;
                            margin: auto;
                            padding: 35px;
                            border-radius: 15px;
                        }

                        h1 {
                            color: green;
                        }

                        .details {
                            background: #f0f8f0;
                            padding: 20px;
                            border-radius: 10px;
                            margin-top: 20px;
                            text-align: left;
                        }

                        .button {
                            display: inline-block;
                            margin-top: 20px;
                            padding: 12px 25px;
                            background: #222;
                            color: white;
                            text-decoration: none;
                            border-radius: 6px;
                        }

                    </style>

                </head>

                <body>

                    <div class="box">

                        <h1>
                            ✅ Attendance Submitted!
                        </h1>

                        <div class="details">

                            <p>
                                <b>Name:</b>
                                {{ name }}
                            </p>

                            <p>
                                <b>Register Number:</b>
                                {{ student_id }}
                            </p>

                            <p>
                                <b>Date:</b>
                                {{ date }}
                            </p>

                            <p>
                                <b>Time:</b>
                                {{ time }}
                            </p>

                        </div>

                       <a
                       class="button"
                       href="/"
                       >
                       🏠 Back to Home
                       </a>

                    </div>

                </body>

                </html>
                """,
                name=student[1],
                student_id=student[0],
                date=date,
                time=time
            )


        # --------------------------------
        # REGISTER NUMBER
        # --------------------------------

        if not student_id:

            return render_template_string(
                """
                <html>

                <body style="
                    font-family:Arial;
                    text-align:center;
                    margin-top:80px;
                ">

                <h2 style="color:red;">
                    ❌ Please enter your register number
                </h2>

                <a href="/scan">
                    Try Again
                </a>

                </body>

                </html>
                """
            )


        # --------------------------------
        # FIND STUDENT
        # --------------------------------

        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT student_id, name
            FROM students
            WHERE student_id = ?
            """,
            (student_id,)
        )

        student = cursor.fetchone()

        conn.close()


        if not student:

            return render_template_string(
                """
                <html>

                <body style="
                    font-family:Arial;
                    background:#f4f6f8;
                    text-align:center;
                    padding-top:80px;
                ">

                <div style="
                    background:white;
                    max-width:500px;
                    margin:auto;
                    padding:35px;
                    border-radius:15px;
                ">

                <h1 style="color:red;">
                    ❌ Invalid Register Number
                </h1>

                <p>
                    Please enter a valid register number.
                </p>

                <br>

                <a href="/scan">
                    ← Try Again
                </a>

                </div>

                </body>

                </html>
                """
            )


        # --------------------------------
        # CONFIRMATION PAGE
        # --------------------------------

        now = datetime.now()

        return render_template_string(
            """
            <!DOCTYPE html>

            <html>

            <head>

                <title>Confirm Attendance</title>

                <style>

                    body {
                        font-family: Arial;
                        background: #f4f6f8;
                        margin: 0;
                        padding: 40px 20px;
                    }

                    .box {
                        max-width: 600px;
                        margin: auto;
                        background: white;
                        padding: 35px;
                        border-radius: 15px;
                    }

                    h1 {
                        text-align: center;
                    }

                    .details {
                        background: #f5f5f5;
                        padding: 20px;
                        border-radius: 10px;
                        margin-top: 25px;
                    }

                    .row {
                        padding: 12px 0;
                        border-bottom: 1px solid #ddd;
                        font-size: 17px;
                    }

                    .confirm {
                        width: 100%;
                        padding: 15px;
                        margin-top: 25px;
                        background: green;
                        color: white;
                        border: none;
                        border-radius: 8px;
                        font-size: 18px;
                    }

                    .cancel {
                        display: block;
                        text-align: center;
                        margin-top: 15px;
                        padding: 13px;
                        background: #555;
                        color: white;
                        text-decoration: none;
                        border-radius: 8px;
                    }

                </style>

            </head>

            <body>

                <div class="box">

                    <h1>
                        📱 Confirm Attendance
                    </h1>

                    <p style="text-align:center;">
                        Please check your details
                        before submitting.
                    </p>

                    <div class="details">

                        <div class="row">
                            <b>👤 Name:</b>
                            {{ name }}
                        </div>

                        <div class="row">
                            <b>🆔 Register Number:</b>
                            {{ student_id }}
                        </div>

                        <div class="row">
                            <b>📅 Date:</b>
                            {{ date }}
                        </div>

                        <div class="row">
                            <b>⏰ Time:</b>
                            {{ time }}
                        </div>

                    </div>

                    <form method="POST">

                        <input
                            type="hidden"
                            name="student_id"
                            value="{{ student_id }}"
                        >

                        <input
                            type="hidden"
                            name="confirm"
                            value="yes"
                        >

                        <button
                            type="submit"
                            class="confirm"
                        >
                            ✅ Confirm Attendance
                        </button>

                    </form>

                    <a
                        href="/scan"
                        class="cancel"
                    >
                        ❌ Cancel / Enter Again
                    </a>

                </div>

            </body>

            </html>
            """,
            name=student[1],
            student_id=student[0],
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S")
        )


    # --------------------------------
    # GET ATTENDANCE PAGE
    # --------------------------------

    html = """
    <!DOCTYPE html>

    <html>

    <head>

        <title>Mark Attendance</title>

        <style>

            body {
                font-family: Arial;
                background: #f4f6f8;
                margin: 30px;
            }

            .box {
                max-width: 600px;
                margin: 60px auto;
                background: white;
                padding: 35px;
                border-radius: 15px;
            }

            h1 {
                text-align: center;
            }

            input {
                width: 100%;
                box-sizing: border-box;
                padding: 15px;
                font-size: 18px;
                margin-top: 20px;
                margin-bottom: 20px;
                border: 1px solid #aaa;
                border-radius: 8px;
            }

            button {
                width: 100%;
                padding: 15px;
                background: green;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 18px;
            }

            .back {
                display: block;
                text-align: center;
                margin-top: 20px;
                color: #333;
            }

        </style>

    </head>

    <body>

        <div class="box">

            <h1>
                📱 Smart QR Attendance
            </h1>

            <p style="text-align:center;">
                Enter your Register Number
            </p>

            <form method="POST">

                <input
                    type="text"
                    name="student_id"
                    placeholder="Enter Register Number"
                    required
                    autocomplete="off"
                >

                <button type="submit">
                    🔍 Check Details
                </button>

            </form>

            <a
                href="/"
                class="back"
            >
                ← Back to Home
            </a>

        </div>

    </body>

    </html>
    """

    return render_template_string(html)


# --------------------------------
# MONTHLY ATTENDANCE
# --------------------------------

@app.route(
    "/monthly",
    methods=["GET", "POST"]
)
def monthly():

    # QR protection
    if not qr_required():

        return render_template_string(
            """
            <html>

            <body style="
                font-family:Arial;
                text-align:center;
                margin-top:100px;
            ">

            <h1 style="color:red;">
                🔒 Access Denied
            </h1>

            <p>
                Please scan the Attendance QR Code first.
            </p>

            </body>

            </html>
            """
        )


    result = None
    error = None


    if request.method == "POST":

        student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        month = request.form.get(
            "month",
            ""
        )

        working_days = request.form.get(
            "working_days",
            ""
        ).strip()


        if (
            not student_id
            or not month
            or not working_days
        ):

            error = "Please enter all details."

        else:

            try:

                working_days = int(
                    working_days
                )

                if working_days <= 0:

                    error = (
                        "Working days must be greater than 0."
                    )

                else:

                    conn = connect_db()
                    cursor = conn.cursor()


                    cursor.execute(
                        """
                        SELECT student_id, name
                        FROM students
                        WHERE student_id = ?
                        """,
                        (student_id,)
                    )

                    student = cursor.fetchone()


                    if not student:

                        error = (
                            "❌ Register number not found."
                        )

                    else:

                        cursor.execute(
                            """
                            SELECT COUNT(DISTINCT date)
                            FROM attendance
                            WHERE student_id = ?
                            AND substr(date, 1, 7) = ?
                            """,
                            (
                                student_id,
                                month
                            )
                        )

                        present_days = (
                            cursor.fetchone()[0]
                        )


                        absent_days = (
                            working_days -
                            present_days
                        )

                        if absent_days < 0:
                            absent_days = 0


                        percentage = (
                            present_days /
                            working_days
                        ) * 100


                        result = {
                            "student_id":
                                student[0],

                            "name":
                                student[1],

                            "month":
                                month,

                            "working_days":
                                working_days,

                            "present_days":
                                present_days,

                            "absent_days":
                                absent_days,

                            "percentage":
                                round(
                                    percentage,
                                    2
                                )
                        }


                    conn.close()


            except ValueError:

                error = (
                    "Working days must be a number."
                )


    html = """
    <!DOCTYPE html>

    <html>

    <head>

        <title>Monthly Attendance</title>

        <style>

            body {
                font-family: Arial;
                background: #f4f6f8;
                margin: 0;
                padding: 30px;
            }

            .box {
                max-width: 650px;
                margin: 30px auto;
                background: white;
                padding: 35px;
                border-radius: 15px;
            }

            h1 {
                text-align: center;
            }

            label {
                display: block;
                margin-top: 18px;
                margin-bottom: 7px;
                font-weight: bold;
            }

            input {
                width: 100%;
                box-sizing: border-box;
                padding: 14px;
                font-size: 16px;
                border: 1px solid #aaa;
                border-radius: 7px;
            }

            button {
                width: 100%;
                padding: 15px;
                margin-top: 25px;
                background: green;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 18px;
            }

            .error {
                background: #ffe6e6;
                color: red;
                padding: 15px;
                border-radius: 8px;
                margin-top: 20px;
                text-align: center;
            }

            .result {
                margin-top: 30px;
                padding: 25px;
                background: #f0fff0;
                border-radius: 12px;
            }

            .row {
                padding: 12px 0;
                border-bottom: 1px solid #ddd;
                font-size: 17px;
            }

            .percentage {
                margin-top: 20px;
                padding: 20px;
                background: green;
                color: white;
                text-align: center;
                border-radius: 10px;
                font-size: 28px;
                font-weight: bold;
            }

            .back {
                display: block;
                text-align: center;
                margin-top: 20px;
                color: #222;
                text-decoration: none;
            }

        </style>

    </head>

    <body>

        <div class="box">

            <h1>
                📊 Monthly Attendance
            </h1>

            <p style="text-align:center;">
                Check one student's monthly attendance percentage.
            </p>


            <form method="POST">

                <label>
                    🆔 Register Number
                </label>

                <input
                    type="text"
                    name="student_id"
                    placeholder="Enter Register Number"
                    required
                >


                <label>
                    📅 Select Month
                </label>

                <input
                    type="month"
                    name="month"
                    required
                >


                <label>
                    🏫 Total Working Days
                </label>

                <input
                    type="number"
                    name="working_days"
                    placeholder="Example: 20"
                    min="1"
                    required
                >


                <button type="submit">
                    📊 Calculate Attendance
                </button>

            </form>


            {% if error %}

            <div class="error">
                {{ error }}
            </div>

            {% endif %}


            {% if result %}

            <div class="result">

                <h2 style="text-align:center;">
                    👤 Student Details
                </h2>

                <div class="row">
                    <b>Name:</b>
                    {{ result.name }}
                </div>

                <div class="row">
                    <b>Register Number:</b>
                    {{ result.student_id }}
                </div>

                <div class="row">
                    <b>Month:</b>
                    {{ result.month }}
                </div>

                <div class="row">
                    <b>Total Working Days:</b>
                    {{ result.working_days }}
                </div>

                <div class="row">
                    <b>Present Days:</b>
                    {{ result.present_days }}
                </div>

                <div class="row">
                    <b>Absent Days:</b>
                    {{ result.absent_days }}
                </div>

                <div class="percentage">
                    📊 {{ result.percentage }}%

                    <div style="
                        font-size:15px;
                        margin-top:8px;
                        font-weight:normal;
                    ">
                        Attendance Percentage
                    </div>

                </div>

            </div>

            {% endif %}


            <a
                href="/"
                class="back"
            >
                ← Back to Home
            </a>

        </div>

    </body>

    </html>
    """

    return render_template_string(
        html,
        result=result,
        error=error
    )


# --------------------------------
# ATTENDANCE RECORDS
# --------------------------------

@app.route("/attendance")
def attendance():

    if not qr_required():

        return render_template_string(
            """
            <html>

            <body style="
                font-family:Arial;
                text-align:center;
                margin-top:100px;
            ">

            <h1 style="color:red;">
                🔒 Access Denied
            </h1>

            <p>
                Please scan the Attendance QR Code first.
            </p>

            </body>

            </html>
            """
        )


    records = get_attendance()


    html = """
    <!DOCTYPE html>

    <html>

    <head>

        <title>Attendance Records</title>

        <style>

            body {
                font-family: Arial;
                margin: 30px;
                background: #f4f6f8;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                background: white;
            }

            th {
                background: #222;
                color: white;
            }

            th, td {
                padding: 12px;
                border: 1px solid #ddd;
                text-align: left;
            }

            a {
                display: inline-block;
                margin-top: 20px;
                padding: 12px 20px;
                background: #222;
                color: white;
                text-decoration: none;
                border-radius: 6px;
            }

        </style>

    </head>

    <body>

        <h1>
            📊 Attendance Records
        </h1>

        <table>

            <tr>
                <th>Register Number</th>
                <th>Name</th>
                <th>Date</th>
                <th>Time</th>
            </tr>

            {% for record in records %}

            <tr>

                <td>
                    {{ record[0] }}
                </td>

                <td>
                    {{ record[1] }}
                </td>

                <td>
                    {{ record[2] }}
                </td>

                <td>
                    {{ record[3] }}
                </td>

            </tr>

            {% endfor %}

        </table>


        <a href="/">
            ← Back to Students
        </a>

    </body>

    </html>
    """

    return render_template_string(
        html,
        records=records
    )


# --------------------------------
# START APPLICATION
# --------------------------------

if __name__ == "__main__":

    print(
        "Starting Smart QR Attendance..."
    )

    create_tables()

    import_students()

    print(
        "Application started!"
    )

    print(
        "QR Token:",
        QR_TOKEN
    )

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
