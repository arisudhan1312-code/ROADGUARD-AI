# ROADGUARD AI — Deployment Guide

## Development (Local Testing)

### Start Backend
```bash
cd "D:\New folder\ROADGAURD_\backend"
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Backend: http://127.0.0.1:5000
Health check: http://127.0.0.1:5000/api/health/database

### Start Frontend
Use VS Code Live Server on port 5500.
Open: http://127.0.0.1:5500/frontend/login.html

---

## Free Prototype Deployment

### Step 1: Deploy MySQL (Railway — Free tier)
1. Go to https://railway.app → Sign up with GitHub
2. New Project → Add MySQL
3. Copy: MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE from Railway dashboard
4. Run your SQL: paste roadguard.sql + push_migration.sql in Railway's Query panel

### Step 2: Deploy Flask Backend (Render — Free tier)
1. Go to https://render.com → Sign up with GitHub
2. New → Web Service → Connect arisudhan1312-code/ROADGUARD-AI
3. Settings:
   - Runtime: Python 3
   - Build Command: pip install -r backend/requirements.txt
   - Start Command: cd backend && waitress-serve --port=$PORT app:app
4. Environment Variables → Add all from .env.example with real values
5. Your backend URL: https://your-service.onrender.com

### Step 3: Deploy Frontend (Netlify — Free)
1. Go to https://netlify.com → Sign up with GitHub
2. New site → Import from GitHub → Select your repo
3. Publish directory: frontend
4. After deploy, your frontend URL: https://your-site.netlify.app
5. Update FRONTEND_ORIGINS in Render to include your Netlify URL

### Step 4: Connect Frontend to Backend
In frontend/js/api-config.js, the defaultBase already handles this correctly for production.
OR add a meta tag to each HTML page:
<meta name="roadguard-api-base" content="https://your-service.onrender.com/api">

### Production Checklist
- [ ] COOKIE_SECURE=true in production .env
- [ ] FLASK_DEBUG=false
- [ ] Strong SECRET_KEY (32+ chars)
- [ ] Production MySQL credentials set
- [ ] VAPID keys set (see VAPID_SETUP.md)
- [ ] Run push_migration.sql on production DB
- [ ] Test /api/health/database on live URL
- [ ] Test all 4 role logins
- [ ] Test push notifications (needs HTTPS)

---

## Push to GitHub
```bash
cd "D:\New folder\ROADGAURD_"
git add .
git commit -m "ROADGUARD AI: Complete implementation"
git push origin main
```
If prompted for password, use a GitHub Personal Access Token:
https://github.com/settings/tokens → New token → repo scope
