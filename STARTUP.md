# ROADGUARD AI — How to Start the Backend

## Step 1: Open VS Code terminal and go to backend
```
cd "D:\New folder\ROADGAURD_\backend"
```

## Step 2: Activate virtual environment
```
.venv\Scripts\activate
```
You will see (.venv) in the prompt.

## Step 3: Install dependencies
```
pip install -r requirements.txt
```

## Step 4: Start the backend
```
python app.py
```
You should see: Running on http://127.0.0.1:5000

## Step 5: Test database connection
Open browser: http://127.0.0.1:5000/api/health/database
You should see: {"status":"ok","database":"connected"}

## Step 6: Fix Admin login (run once in MySQL Workbench)
```sql
USE roadguard_ai;
DELETE FROM users WHERE email = 'ariharasudhan1312@gmail.com' AND role = 'admin';
INSERT INTO users (full_name, email, mobile, password_hash, role, status)
VALUES (
  'Admin',
  'ariharasudhan1312@gmail.com',
  '0000000000',
  'pbkdf2:sha256:260000$kYY1O6zu7fJ2YtFP$9f4128f9b772781cdca7c75c27ea47582ead82f691ec7a01587b42ed812789ad',
  'admin',
  'active'
);
```

## Step 7: Open frontend
Open VS Code Live Server on port 5500.
Navigate to: http://127.0.0.1:5500/frontend/login.html

Admin login: ariharasudhan1312@gmail.com / arisudhan2008
