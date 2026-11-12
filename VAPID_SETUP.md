# ROADGUARD AI — VAPID Key Setup

## Generate VAPID keys (run once)
```
cd "D:\New folder\ROADGAURD_\backend"
.venv\Scripts\activate
pip install pywebpush cryptography
python -c "from py_vapid import Vapid; v=Vapid(); v.generate_keys(); print('PRIVATE:',v.private_pem().decode()); print('PUBLIC:',v.public_key.public_bytes_raw().hex())"
```
OR use: https://web-push-codelab.glitch.me/

## Add to backend/.env
VAPID_PRIVATE_KEY=your_private_key_here
VAPID_PUBLIC_KEY=your_public_key_here
VAPID_CLAIM_EMAIL=ariharasudhan1312@gmail.com

## Run SQL migration in MySQL Workbench
SOURCE D:/New folder/ROADGAURD_/database/push_migration.sql;

## Notes
- Push only works on HTTPS in production (Chrome/Edge allow http://localhost in dev)
- Drivers must click "Enable Background Notifications" and allow permission
- Notifications are delivered by the browser — not guaranteed on all devices
