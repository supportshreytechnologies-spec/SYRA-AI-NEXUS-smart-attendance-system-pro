# SYRA AI NEXUS — Customer Build

This build keeps the existing dashboard and real face-attendance workflow, with no paid feature gating.

## Real attendance
Attendance uses the live camera, server-side face detection, randomized movement/liveness checks, and LBPH recognition against the face captured when a member is enrolled. Attendance is written only after a real enrolled-face match.

## QR center
Admin accounts can generate:
- Member Registration QR — opens a face-enrollment page and saves the captured face into the attendance database.
- Attendance Marking QR — generates a fresh 15-minute attendance session. Members scan it and use the live face scanner; no manual/dummy attendance path is used.

The supplied database promotes the first existing account to admin. You can set `ADMIN_EMAIL` in `.env` to explicitly designate an admin account.

## Password reset
Password reset uses a one-time secure reset link sent through the Resend API.
- `RESEND_API_KEY` is required.
- `RESEND_FROM_EMAIL` defaults to `onboarding@resend.dev` for Resend testing; use a sender on a verified domain for production.
- `PUBLIC_BASE_URL` is optional.

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Use HTTPS for public/LAN camera deployment.
