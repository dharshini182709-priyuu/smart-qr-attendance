import sqlite3

DB_NAME = "attendance.db"


# -----------------------------
# CONNECT TO DATABASE
# -----------------------------

def connect_db():
    return sqlite3.connect(DB_NAME)


# -----------------------------
# CREATE TABLES
# -----------------------------

def create_tables():

    conn = connect_db()
    cursor = conn.cursor()

    # Students table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)

    # Attendance table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            FOREIGN KEY (student_id)
            REFERENCES students(student_id)
        )
    """)

    conn.commit()
    conn.close()

    print("Database tables created successfully!")


# -----------------------------
# ADD STUDENT
# -----------------------------

def add_student(student_id, name):

    conn = connect_db()

    conn.execute("""
        INSERT OR REPLACE INTO students
        (student_id, name)
        VALUES (?, ?)
    """, (student_id, name))

    conn.commit()
    conn.close()


# -----------------------------
# GET ALL STUDENTS
# -----------------------------

def get_students():

    conn = connect_db()

    students = conn.execute("""
        SELECT student_id, name
        FROM students
        ORDER BY student_id
    """).fetchall()

    conn.close()

    return students


# -----------------------------
# MARK ATTENDANCE
# -----------------------------

def mark_attendance(student_id, date, time):

    conn = connect_db()

    # Prevent duplicate attendance
    existing = conn.execute("""
        SELECT id
        FROM attendance
        WHERE student_id = ?
        AND date = ?
    """, (student_id, date)).fetchone()

    if existing:

        conn.close()

        return False

    conn.execute("""
        INSERT INTO attendance
        (student_id, date, time)
        VALUES (?, ?, ?)
    """, (student_id, date, time))

    conn.commit()
    conn.close()

    return True


# -----------------------------
# GET ATTENDANCE
# -----------------------------

def get_attendance():

    conn = connect_db()

    records = conn.execute("""
        SELECT
            attendance.student_id,
            students.name,
            attendance.date,
            attendance.time
        FROM attendance
        JOIN students
        ON attendance.student_id = students.student_id
        ORDER BY attendance.date DESC,
                 attendance.time DESC
    """).fetchall()

    conn.close()

    return records


# -----------------------------
# TEST DATABASE
# -----------------------------

if __name__ == "__main__":

    create_tables()

    print("Database is ready!")

    students = get_students()

    print("Total students:", len(students))