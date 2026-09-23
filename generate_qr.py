import qrcode

url = "https://smart-qr-attendance-vccq.onrender.com/access/SMARTQR2026"
qr = qrcode.make(url)

qr.save("common_attendance_qr.png")

print("ONE COMMON QR CREATED!")
print("URL:", url)
