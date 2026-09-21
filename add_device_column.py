import sqlite3

conn = sqlite3.connect("attendance.db")
cursor = conn.cursor()

try:
    cursor.execute("""
        ALTER TABLE attendance
        ADD COLUMN device_id TEXT
    """)
    print("✅ device_id column added successfully!")

except sqlite3.OperationalError:
    print("ℹ️ device_id column already exists.")

conn.commit()
conn.close()