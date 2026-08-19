import qrcode

url = "https://handyman-presuming-slit.ngrok-free.dev/access/SMARTQR2026"
qr = qrcode.make(url)

qr.save("common_attendance_qr.png")

print("ONE COMMON QR CREATED!")
print("URL:", url)