import os, sqlite3, secrets, time, hashlib, re, base64, io
from datetime import datetime, date
from email.message import EmailMessage
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
DB = os.path.join(BASE, "data", "syra.db")
FACE_DIR = os.path.join(BASE, "data", "faces")
os.makedirs(FACE_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-syra-secret")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("COOKIE_SECURE", "false").lower() == "true"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support.shreytechnologies@gmail.com")
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "xxxxxxxxxx")
WHATSAPP_URL = os.getenv("WHATSAPP_GROUP_URL", "").strip()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        mobile TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        photo TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        expires INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(member_id) REFERENCES members(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS attendance_members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        mobile TEXT UNIQUE,
        face_image TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS member_settings(
        member_id INTEGER PRIMARY KEY,
        whatsapp_url TEXT DEFAULT '',
        FOREIGN KEY(member_id) REFERENCES members(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        marked_at TEXT NOT NULL,
        work_date TEXT NOT NULL,
        UNIQUE(member_id, work_date),
        FOREIGN KEY(member_id) REFERENCES attendance_members(id)
    )""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(members)").fetchall()}
    if "role" not in cols:
        con.execute("ALTER TABLE members ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    # The existing first account remains the owner/admin of this supplied build.
    if ADMIN_EMAIL:
        con.execute("UPDATE members SET role='admin' WHERE lower(email)=?", (ADMIN_EMAIL,))
    elif con.execute("SELECT COUNT(*) FROM members WHERE role='admin'").fetchone()[0] == 0:
        first = con.execute("SELECT id FROM members ORDER BY id LIMIT 1").fetchone()
        if first:
            con.execute("UPDATE members SET role='admin' WHERE id=?", (first[0],))
    con.execute("""CREATE TABLE IF NOT EXISTS qr_tokens(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL,
        expires INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.commit()
    con.close()

def phash(password):
    return hashlib.sha256(password.encode()).hexdigest()

def clean_mobile(value):
    return "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")

def make_reset_token(member_id):
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    con = db()
    con.execute("DELETE FROM password_reset_tokens WHERE member_id=? OR expires<? OR used=1", (member_id, int(time.time())))
    con.execute("INSERT INTO password_reset_tokens(member_id,token_hash,expires,used,created_at) VALUES(?,?,?,?,?)",
                (member_id, token_hash, int(time.time()) + 1800, 0, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    con.close()
    return raw

def send_reset_email(email, reset_url):
    """Send a one-time password reset link through Resend (no SMTP/Gmail login)."""
    subject = "SYRA - Reset your password"
    body = f"""Hello,

We received a request to reset your SYRA password.

Open this secure link to choose a new password:
{reset_url}

This link expires in 30 minutes and can only be used once.

If you did not request this, you can safely ignore this email.

SYRA
"""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
    if not api_key:
        return False, "RESEND_API_KEY is missing in .env"
    if "@" not in from_email:
        return False, "RESEND_FROM_EMAIL must be a valid email address"
    try:
        import requests
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_email,
                "to": [email],
                "subject": subject,
                "text": body,
            },
            timeout=20,
        )
        if response.status_code < 200 or response.status_code >= 300:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            return False, f"Resend API error ({response.status_code}): {detail}"
        app.logger.info("Password reset email sent to %s via Resend", email)
        return True, ""
    except Exception as e:
        app.logger.exception("PASSWORD RESET EMAIL FAILED")
        return False, f"{type(e).__name__}: {e}"

def reset_token_valid(raw_token):
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    con = db()
    row = con.execute("SELECT * FROM password_reset_tokens WHERE token_hash=? AND used=0 AND expires>=?",
                      (token_hash, int(time.time()))).fetchone()
    con.close()
    return row


def require_login():
    return bool(session.get("member_id"))

def current_account():
    if not require_login():
        return None
    con = db()
    row = con.execute("SELECT id,name,email,role FROM members WHERE id=?", (session["member_id"],)).fetchone()
    con.close()
    return row

def is_admin():
    row = current_account()
    return bool(row and row["role"] == "admin")

def require_admin_json():
    if not require_login():
        return jsonify(ok=False, error="Login required."), 401
    if not is_admin():
        return jsonify(ok=False, error="Admin access required."), 403
    return None

def make_qr_token(kind, ttl_seconds):
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    con = db()
    con.execute("DELETE FROM qr_tokens WHERE expires<?", (int(time.time()),))
    con.execute("INSERT INTO qr_tokens(token_hash,kind,expires,created_at) VALUES(?,?,?,?)",
                (token_hash, kind, int(time.time()) + ttl_seconds, datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()
    return raw

def valid_qr_token(raw, kind):
    if not raw:
        return False
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    con = db()
    row = con.execute("SELECT id FROM qr_tokens WHERE token_hash=? AND kind=? AND expires>=?",
                      (token_hash, kind, int(time.time()))).fetchone()
    con.close()
    return bool(row)

def qr_png(text):
    try:
        import qrcode
        img = qrcode.make(text)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except ImportError:
        raise RuntimeError("QR support is missing. Install requirements.txt")

def cv2_modules():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError:
        return None, None

def detect_face(gray, cv2):
    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.10, minNeighbors=7, minSize=(100, 100))
    if len(faces) != 1:
        return None
    return faces[0]

def decode_frame(data_url):
    cv2, np = cv2_modules()
    if cv2 is None:
        raise RuntimeError("OpenCV is not installed. Run pip install -r requirements.txt")
    if not data_url or "," not in data_url:
        raise ValueError("Invalid camera frame.")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    arr = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Camera frame could not be decoded.")
    return frame

def rebuild_face_model():
    cv2, np = cv2_modules()
    if cv2 is None or not hasattr(cv2, "face"):
        raise RuntimeError("OpenCV contrib is required for face recognition.")
    con = db()
    rows = con.execute("SELECT id,face_image FROM attendance_members ORDER BY id").fetchall()
    con.close()
    faces, labels = [], []
    for row in rows:
        path = row["face_image"]
        if not os.path.exists(path):
            continue
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        img = cv2.equalizeHist(img)
        faces.append(img)
        labels.append(int(row["id"]))
    if not faces:
        return False
    model = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    model.train(faces, np.array(labels))
    model_path = os.path.join(FACE_DIR, "model.yml")
    model.write(model_path)
    return True

def recognize_frame(frame):
    cv2, np = cv2_modules()
    if cv2 is None or not hasattr(cv2, "face"):
        raise RuntimeError("OpenCV contrib is required for face recognition.")
    model_path = os.path.join(FACE_DIR, "model.yml")
    if not os.path.exists(model_path):
        return None, None, "No enrolled faces. Add a member first."
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    face = detect_face(gray, cv2)
    if face is None:
        return None, None, "No face detected. Center one face in the camera."
    x, y, w, h = face
    crop = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
    crop = cv2.equalizeHist(crop)
    model = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
    model.read(model_path)
    label, confidence = model.predict(crop)
    # LBPH confidence is a distance: lower means more similar.
    if confidence > float(os.getenv("FACE_CONFIDENCE_THRESHOLD", "55")):
        return None, confidence, "Face not recognized. Attendance was NOT marked."
    con = db()
    row = con.execute("SELECT id,name,email,mobile FROM attendance_members WHERE id=?", (int(label),)).fetchone()
    con.close()
    if not row:
        return None, confidence, "Recognized face has no active member record."
    return dict(row), confidence, None

init_db()

@app.route("/")
def home():
    if require_login():
        return redirect(url_for("dashboard"))
    return render_template("login.html", support_email=SUPPORT_EMAIL, support_phone=SUPPORT_PHONE)

@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect(url_for("home"))
    con = db()
    try:
        member = con.execute("SELECT id,name,email,mobile,photo,created_at,role FROM members WHERE id=?", (session["member_id"],)).fetchone()
        count = con.execute("SELECT COUNT(*) c FROM attendance_members").fetchone()["c"]
        present = con.execute("SELECT COUNT(*) c FROM attendance WHERE work_date=?", (date.today().isoformat(),)).fetchone()["c"]
        absent = max(0, count - present)
        settings = con.execute("SELECT whatsapp_url FROM member_settings WHERE member_id=?", (session["member_id"],)).fetchone()
    finally:
        con.close()
    if not member:
        session.clear()
        return redirect(url_for("home"))
    whatsapp = (settings["whatsapp_url"] if settings and settings["whatsapp_url"] else WHATSAPP_URL)
    return render_template("dashboard.html", member=member, count=count, present=present,
                           absent=absent, whatsapp=whatsapp,
                           is_admin=is_admin(),
                           support_email=SUPPORT_EMAIL, support_phone=SUPPORT_PHONE)

@app.post("/api/register/start")
def register_start():
    data = request.get_json() or {}
    name, email = (data.get("name") or "").strip(), (data.get("email") or "").strip().lower()
    mobile, password = clean_mobile(data.get("mobile")), data.get("password") or ""
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(mobile) < 8 or len(password) < 6:
        return jsonify(ok=False, error="Name, valid email, mobile and 6+ character password required."), 400
    con = db()
    exists = con.execute("SELECT id FROM members WHERE lower(email)=? OR mobile=?", (email, mobile)).fetchone()
    if exists:
        con.close()
        return jsonify(ok=False, error="Account already exists. Use Login or Reset Password."), 409
    try:
        cur = con.execute("INSERT INTO members(name,email,mobile,password_hash,created_at) VALUES(?,?,?,?,?)",
                          (name, email, mobile, phash(password), datetime.now().isoformat(timespec="seconds")))
        con.commit()
        member_id = cur.lastrowid
    except sqlite3.IntegrityError:
        con.close()
        return jsonify(ok=False, error="An account with this email or mobile already exists."), 409
    con.close()
    session["member_id"] = member_id
    return jsonify(ok=True, message="Account created successfully.")

@app.post("/api/login")
def login():
    data=request.get_json() or {}; ident=(data.get("identifier") or "").strip().lower(); password=data.get("password") or ""
    con=db(); row=con.execute("SELECT * FROM members WHERE lower(email)=? OR mobile=?", (ident,clean_mobile(ident))).fetchone(); con.close()
    if not row or not secrets.compare_digest(row["password_hash"],phash(password)):
        return jsonify(ok=False,error="Invalid login details."),401
    session["member_id"]=row["id"]
    return jsonify(ok=True,name=row["name"],is_admin=(row["role"] == "admin" if "role" in row.keys() else False))

@app.post("/api/reset/start")
def reset_start():
    data = request.get_json() or {}
    ident = (data.get("identifier") or "").strip().lower()
    con = db()
    row = con.execute("SELECT * FROM members WHERE lower(email)=? OR mobile=?", (ident, clean_mobile(ident))).fetchone()
    con.close()
    if not row:
        return jsonify(ok=False, error="No registered account found."), 404

    raw_token = make_reset_token(row["id"])
    public_base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public_base:
        reset_url = f"{public_base}/reset-password?token={raw_token}"
    else:
        reset_url = url_for("reset_password_page", token=raw_token, _external=True)
    sent, mail_error = send_reset_email(row["email"], reset_url)
    if not sent:
        app.logger.error("RESET START FAILED: %s", mail_error)
        return jsonify(ok=False, error=f"Reset email could not be sent: {mail_error}"), 503
    return jsonify(ok=True, message="Password reset link sent to your registered email.")

@app.get("/reset-password")
def reset_password_page():
    token = request.args.get("token", "")
    if not reset_token_valid(token):
        return "<h2>SYRA</h2><p>This password reset link is invalid, expired, or already used.</p><p><a href='/'>Back to login</a></p>", 400
    return render_template("reset_password.html", token=token)

@app.post("/api/reset/finish")
def reset_finish():
    data = request.get_json() or {}
    token = data.get("token") or ""
    new_password = data.get("password") or ""
    if len(new_password) < 6:
        return jsonify(ok=False, error="New password must be at least 6 characters."), 400
    row = reset_token_valid(token)
    if not row:
        return jsonify(ok=False, error="Reset link is invalid, expired, or already used."), 400
    con = db()
    con.execute("UPDATE members SET password_hash=? WHERE id=?", (phash(new_password), row["member_id"]))
    con.execute("UPDATE password_reset_tokens SET used=1 WHERE id=?", (row["id"],))
    con.commit()
    con.close()
    return jsonify(ok=True, message="Password reset complete. You can login now.")

@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@app.get("/api/support")
def support():
    return jsonify(email=SUPPORT_EMAIL,phone=SUPPORT_PHONE)

@app.get("/api/profile")
def profile():
    if not require_login(): return jsonify(ok=False),401
    con=db(); row=con.execute("SELECT id,name,email,mobile,photo,created_at,role FROM members WHERE id=?",(session["member_id"],)).fetchone(); con.close()
    return jsonify(ok=True,member=dict(row) if row else None)

@app.get("/api/members")
def members():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    con=db()
    rows=con.execute("""SELECT m.id,m.name,m.email,m.mobile,m.created_at,
                        EXISTS(SELECT 1 FROM attendance a WHERE a.member_id=m.id AND a.work_date=?) AS present
                        FROM attendance_members m ORDER BY m.id DESC""",(date.today().isoformat(),)).fetchall()
    con.close()
    return jsonify(ok=True,members=[dict(r) for r in rows])

@app.post("/api/members/enroll")
def enroll_member():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    data=request.get_json() or {}
    name=(data.get("name") or "").strip(); email=(data.get("email") or "").strip().lower() or None
    mobile=clean_mobile(data.get("mobile")) or None
    if not name: return jsonify(ok=False,error="Member name is required."),400
    if len(name) > 100: return jsonify(ok=False,error="Member name is too long."),400
    if not data.get("image"): return jsonify(ok=False,error="Capture a real face photo first."),400
    cv2,np=cv2_modules()
    if cv2 is None: return jsonify(ok=False,error="OpenCV is missing. Install requirements.txt."),500
    try:
        frame=decode_frame(data["image"])
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); face=detect_face(gray,cv2)
        if face is None: return jsonify(ok=False,error="Enrollment requires exactly one clearly visible face."),400
        x,y,w,h=face
        image_area=gray.shape[0]*gray.shape[1]
        if (w*h)/max(image_area,1) < 0.06: return jsonify(ok=False,error="Move closer so the face fills the camera frame."),400
        crop=cv2.resize(gray[y:y+h,x:x+w],(200,200)); crop=cv2.equalizeHist(crop)
        con=db()
        if email and con.execute("SELECT id FROM attendance_members WHERE email=?",(email,)).fetchone():
            con.close(); return jsonify(ok=False,error="A member with this email already exists."),409
        if mobile and con.execute("SELECT id FROM attendance_members WHERE mobile=?",(mobile,)).fetchone():
            con.close(); return jsonify(ok=False,error="A member with this mobile already exists."),409
        cur=con.execute("INSERT INTO attendance_members(name,email,mobile,face_image,created_at) VALUES(?,?,?,?,?)",
                        (name,email,mobile,"",datetime.now().isoformat(timespec="seconds")))
        member_id=cur.lastrowid
        path=os.path.join(FACE_DIR,f"member_{member_id}.png")
        if not cv2.imwrite(path,crop):
            con.rollback(); con.close(); return jsonify(ok=False,error="Could not save face image."),500
        con.execute("UPDATE attendance_members SET face_image=? WHERE id=?",(path,member_id))
        con.commit(); con.close()
        rebuild_face_model()
        return jsonify(ok=True,message=f"{name} enrolled successfully.",member_id=member_id)
    except Exception as e:
        return jsonify(ok=False,error=str(e)),500

@app.post("/api/attendance/liveness")
def attendance_liveness():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    data=request.get_json() or {}; frames=data.get("frames") or []; direction=data.get("direction")
    if not isinstance(frames,list) or len(frames)<5: return jsonify(ok=False,error="Live verification needs multiple camera frames."),400
    cv2,np=cv2_modules()
    if cv2 is None: return jsonify(ok=False,error="OpenCV is missing."),500
    centers=[]; sizes=[]
    try:
        for encoded in frames:
            frame=decode_frame(encoded); gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); face=detect_face(gray,cv2)
            if face is None: return jsonify(ok=False,error="Liveness failed: keep exactly one face visible."),400
            x,y,w,h=face; centers.append(x+w/2); sizes.append(w*h/(gray.shape[0]*gray.shape[1]))
        delta=centers[-1]-centers[0]; span=max(centers)-min(centers)
        if span < max(18, frames and 0.04*640): return jsonify(ok=False,error="Liveness failed: make a small natural head movement when prompted."),400
        if direction == "left" and delta > -12: return jsonify(ok=False,error="Liveness failed: move slightly left."),400
        if direction == "right" and delta < 12: return jsonify(ok=False,error="Liveness failed: move slightly right."),400
        if max(sizes)-min(sizes) < 0.005: return jsonify(ok=False,error="Liveness failed: camera frames look too static."),400
        token=secrets.token_urlsafe(24); session["liveness_token"]=token; session["liveness_time"]=int(time.time())
        return jsonify(ok=True,token=token)
    except Exception as e:
        return jsonify(ok=False,error=str(e)),400

@app.post("/api/attendance/recognize")
def attendance_recognize():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    data=request.get_json() or {}
    token=data.get("liveness_token")
    if not token or not secrets.compare_digest(str(token), str(session.get("liveness_token", ""))) or int(time.time())-int(session.get("liveness_time",0))>90:
        return jsonify(ok=False,error="Live verification required before attendance can be marked."),400
    session.pop("liveness_token",None); session.pop("liveness_time",None)
    try:
        frame=decode_frame(data.get("image"))
        member,confidence,error=recognize_frame(frame)
        if error: return jsonify(ok=False,error=error,confidence=confidence),400
        today=date.today().isoformat()
        con=db()
        try:
            cur=con.execute("INSERT INTO attendance(member_id,marked_at,work_date) VALUES(?,?,?)",
                            (member["id"],datetime.now().isoformat(timespec="seconds"),today))
            con.commit(); newly=True
        except sqlite3.IntegrityError:
            newly=False
        con.close()
        return jsonify(ok=True,newly_marked=newly,member=member,confidence=round(float(confidence),2),
                       message="Attendance marked successfully." if newly else "Attendance already marked today.")
    except Exception as e:
        return jsonify(ok=False,error=str(e)),500

@app.get("/api/attendance/history")
def attendance_history():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    con=db()
    rows=con.execute("""SELECT a.id,m.name,m.email,m.mobile,a.marked_at,a.work_date
                        FROM attendance a JOIN attendance_members m ON m.id=a.member_id
                        ORDER BY a.marked_at DESC LIMIT 200""").fetchall()
    con.close()
    return jsonify(ok=True,records=[dict(r) for r in rows])

@app.get("/api/settings")
def get_settings():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    con=db()
    row=con.execute("SELECT whatsapp_url FROM member_settings WHERE member_id=?",(session["member_id"],)).fetchone()
    con.close()
    return jsonify(ok=True, whatsapp_url=(row["whatsapp_url"] if row else ""))

@app.post("/api/settings")
def save_settings():
    if not require_login(): return jsonify(ok=False,error="Login required."),401
    data=request.get_json() or {}
    whatsapp=(data.get("whatsapp_url") or "").strip()
    if whatsapp and not re.match(r"^https://(chat\.whatsapp\.com/|wa\.me/)", whatsapp, re.I):
        return jsonify(ok=False,error="Enter a valid WhatsApp invite link starting with https://chat.whatsapp.com/ or https://wa.me/"),400
    con=db(); con.execute("INSERT INTO member_settings(member_id,whatsapp_url) VALUES(?,?) ON CONFLICT(member_id) DO UPDATE SET whatsapp_url=excluded.whatsapp_url",(session["member_id"],whatsapp)); con.commit(); con.close()
    return jsonify(ok=True,message="WhatsApp group link saved." if whatsapp else "WhatsApp group link cleared.")
      


@app.get("/admin/qr")
def admin_qr_page():
    if not require_login():
        return redirect(url_for("home"))
    if not is_admin():
        return "<h2>SYRA</h2><p>Admin access required.</p><p><a href='/dashboard'>Back to dashboard</a></p>", 403
    return render_template("admin_qr.html", is_admin=True)

@app.post("/api/admin/qr")
def create_admin_qr():
    denied = require_admin_json()
    if denied:
        return denied
    data = request.get_json() or {}
    kind = data.get("kind")
    if kind not in ("registration", "attendance"):
        return jsonify(ok=False, error="Invalid QR type."), 400
    ttl = 24 * 60 * 60 if kind == "registration" else 15 * 60
    token = make_qr_token(kind, ttl)
    target = url_for("member_register_page", token=token, _external=True) if kind == "registration" else url_for("attendance_scan_page", token=token, _external=True)
    return jsonify(ok=True, kind=kind, url=target, expires_in=ttl)

@app.get("/admin/qr-image")
def admin_qr_image():
    denied = require_admin_json()
    if denied:
        return denied
    target = request.args.get("url", "")
    if not target:
        return jsonify(ok=False, error="QR target is required."), 400
    try:
        return send_file(qr_png(target), mimetype="image/png", max_age=0)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.get("/member-register")
def member_register_page():
    token = request.args.get("token", "")
    if not valid_qr_token(token, "registration"):
        return "<h2>SYRA</h2><p>This registration QR is invalid or expired.</p>", 400
    return render_template("member_register.html", token=token)

@app.post("/api/public/member-register")
def public_member_register():
    data = request.get_json() or {}
    token = data.get("token", "")
    if not valid_qr_token(token, "registration"):
        return jsonify(ok=False, error="Registration QR is invalid or expired. Ask the admin for a new QR."), 400
    name=(data.get("name") or "").strip(); email=(data.get("email") or "").strip().lower() or None
    mobile=clean_mobile(data.get("mobile")) or None
    if not name: return jsonify(ok=False,error="Member name is required."),400
    if len(name) > 100: return jsonify(ok=False,error="Member name is too long."),400
    if not data.get("image"): return jsonify(ok=False,error="Capture a real face photo first."),400
    cv2,np=cv2_modules()
    if cv2 is None: return jsonify(ok=False,error="OpenCV is missing. Install requirements.txt."),500
    try:
        frame=decode_frame(data["image"])
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); face=detect_face(gray,cv2)
        if face is None: return jsonify(ok=False,error="Registration requires exactly one clearly visible face."),400
        x,y,w,h=face; image_area=gray.shape[0]*gray.shape[1]
        if (w*h)/max(image_area,1) < 0.06: return jsonify(ok=False,error="Move closer so the face fills the camera frame."),400
        crop=cv2.resize(gray[y:y+h,x:x+w],(200,200)); crop=cv2.equalizeHist(crop)
        con=db()
        if email and con.execute("SELECT id FROM attendance_members WHERE email=?",(email,)).fetchone():
            con.close(); return jsonify(ok=False,error="A member with this email already exists."),409
        if mobile and con.execute("SELECT id FROM attendance_members WHERE mobile=?",(mobile,)).fetchone():
            con.close(); return jsonify(ok=False,error="A member with this mobile already exists."),409
        cur=con.execute("INSERT INTO attendance_members(name,email,mobile,face_image,created_at) VALUES(?,?,?,?,?)",
                        (name,email,mobile,"",datetime.now().isoformat(timespec="seconds")))
        member_id=cur.lastrowid; path=os.path.join(FACE_DIR,f"member_{member_id}.png")
        if not cv2.imwrite(path,crop):
            con.rollback(); con.close(); return jsonify(ok=False,error="Could not save face image."),500
        con.execute("UPDATE attendance_members SET face_image=? WHERE id=?",(path,member_id))
        con.commit(); con.close(); rebuild_face_model()
        return jsonify(ok=True,message=f"{name} registered successfully. You can now use the attendance scanner.",member_id=member_id)
    except Exception as e:
        return jsonify(ok=False,error=str(e)),500

@app.get("/attendance-scan")
def attendance_scan_page():
    token = request.args.get("token", "")
    if not valid_qr_token(token, "attendance"):
        return "<h2>SYRA</h2><p>This attendance QR is invalid or expired. Ask the admin to generate a new one.</p>", 400
    return render_template("attendance_scan.html", token=token)

@app.post("/api/public/attendance/liveness")
def public_attendance_liveness():
    data=request.get_json() or {}; token=data.get("qr_token"); frames=data.get("frames") or []; direction=data.get("direction")
    if not valid_qr_token(token, "attendance"):
        return jsonify(ok=False,error="Attendance QR is invalid or expired. Ask the admin for a new QR."),400
    if not isinstance(frames,list) or len(frames)<5: return jsonify(ok=False,error="Live verification needs multiple camera frames."),400
    cv2,np=cv2_modules()
    if cv2 is None: return jsonify(ok=False,error="OpenCV is missing."),500
    centers=[]; sizes=[]
    try:
        for encoded in frames:
            frame=decode_frame(encoded); gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); face=detect_face(gray,cv2)
            if face is None: return jsonify(ok=False,error="Liveness failed: keep exactly one face visible."),400
            x,y,w,h=face; centers.append(x+w/2); sizes.append(w*h/(gray.shape[0]*gray.shape[1]))
        delta=centers[-1]-centers[0]; span=max(centers)-min(centers)
        if span < max(18, 0.04*640): return jsonify(ok=False,error="Liveness failed: make a small natural head movement."),400
        if direction == "left" and delta > -12: return jsonify(ok=False,error="Liveness failed: move slightly left."),400
        if direction == "right" and delta < 12: return jsonify(ok=False,error="Liveness failed: move slightly right."),400
        if max(sizes)-min(sizes) < 0.005: return jsonify(ok=False,error="Liveness failed: camera frames look too static."),400
        verify_token=secrets.token_urlsafe(24); session["public_liveness_token"]=verify_token; session["public_liveness_time"]=int(time.time())
        return jsonify(ok=True,token=verify_token)
    except Exception as e:
        return jsonify(ok=False,error=str(e)),400

@app.post("/api/public/attendance/recognize")
def public_attendance_recognize():
    data=request.get_json() or {}; qr_token=data.get("qr_token"); verify_token=data.get("liveness_token")
    if not valid_qr_token(qr_token, "attendance"):
        return jsonify(ok=False,error="Attendance QR is invalid or expired."),400
    if not verify_token or not secrets.compare_digest(str(verify_token),str(session.get("public_liveness_token",""))) or int(time.time())-int(session.get("public_liveness_time",0))>90:
        return jsonify(ok=False,error="Live verification required before attendance can be marked."),400
    session.pop("public_liveness_token",None); session.pop("public_liveness_time",None)
    try:
        frame=decode_frame(data.get("image")); member,confidence,error=recognize_frame(frame)
        if error: return jsonify(ok=False,error=error,confidence=confidence),400
        today=date.today().isoformat(); con=db()
        try:
            con.execute("INSERT INTO attendance(member_id,marked_at,work_date) VALUES(?,?,?)",
                        (member["id"],datetime.now().isoformat(timespec="seconds"),today)); con.commit(); newly=True
        except sqlite3.IntegrityError:
            newly=False
        con.close()
        return jsonify(ok=True,newly_marked=newly,member=member,confidence=round(float(confidence),2),
                       message="Attendance marked successfully." if newly else "Attendance already marked today.")
    except Exception as e:
        return jsonify(ok=False,error=str(e)),500

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
