from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import os
import uuid
import random

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ----------------- SIMPLE USERS (MASTER ONLY) -----------------
# Master is hardcoded; all distributors are in DB
USERS = {
    "master": {"password": "master123", "role": "master"},
}

# ----------------- DB SETUP -----------------
DATABASE_URL = "sqlite:///./ad_monitor.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)


class Screen(Base):
    __tablename__ = "screens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"))
    last_heartbeat_at = Column(DateTime, nullable=True)
    status = Column(String, default="offline")

    company = relationship("Company")


class Ad(Base):
    __tablename__ = "ads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    owner_company_id = Column(Integer, ForeignKey("companies.id"))
    file_url = Column(String)
    duration_sec = Column(Integer)

    owner_company = relationship("Company")


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id = Column(Integer, primary_key=True, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"))
    timestamp = Column(DateTime)
    current_ad_id = Column(Integer, ForeignKey("ads.id"), nullable=True)
    player_status = Column(String)

    screen = relationship("Screen")
    current_ad = relationship("Ad")


class PlaybackEvent(Base):
    __tablename__ = "playback_events"

    id = Column(Integer, primary_key=True, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"))
    ad_id = Column(Integer, ForeignKey("ads.id"))
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    status = Column(String)

    screen = relationship("Screen")
    ad = relationship("Ad")


class Distributor(Base):
    __tablename__ = "distributors"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    company_name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    username = Column(String, nullable=False, unique=True)  # we use email as username
    password = Column(String, nullable=False)  # plaintext for demo; hash in real life
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    is_verified = Column(Boolean, default=False)

    otp_code = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)

    login_otp = Column(String, nullable=True)
    login_otp_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    company = relationship("Company")


Base.metadata.create_all(bind=engine)

# ----------------- FASTAPI APP -----------------
app = FastAPI(title="Ad Monitoring Prototype")

# Static media (videos)
app.mount("/media", StaticFiles(directory="media"), name="media")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------- HELPER: "SEND EMAIL" (SIMULATED) -----------------
def send_email(to_email: str, subject: str, body: str):
    """
    Simulated email sender.
    In real deployment, replace this with actual SMTP / transactional email.
    For now, we just print to the server console so you can see the OTP.
    """
    print("========== EMAIL (SIMULATED) ==========")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(body)
    print("=======================================")


# ----------------- Pydantic Schemas -----------------
class AdOut(BaseModel):
    id: int
    name: str
    owner_company_id: int
    file_url: Optional[str]
    duration_sec: Optional[int]

    class Config:
        from_attributes = True  # pydantic v2 compatible


class PlaylistResponse(BaseModel):
    screen_id: int
    playlist: List[AdOut]


class HeartbeatIn(BaseModel):
    screen_id: int
    current_ad_id: Optional[int] = None
    player_status: Optional[str] = "ok"


class PlaybackEventIn(BaseModel):
    screen_id: int
    ad_id: int
    started_at: datetime
    ended_at: datetime
    status: Optional[str] = "success"


class ScreenCreate(BaseModel):
    name: str
    company_id: int = 1  # default central company


# ----------------- Auth helpers -----------------
def get_current_user(request: Request):
    username = request.cookies.get("username")
    role = request.cookies.get("role")
    if not username or not role:
        return None

    db = next(get_db())

    if role == "master":
        if username in USERS:
            return {"username": username, "role": "master"}
        return None

    if role == "distributor":
        dist = db.query(Distributor).filter(Distributor.username == username).first()
        if dist:
            return {"username": username, "role": "distributor", "distributor_id": dist.id}
        return None

    return None


NAV_HTML = """
<nav style="background:#222;padding:8px 12px;">
  <a href="/dashboard" style="color:#fff;margin-right:10px;text-decoration:none;">Dashboard</a>
  <a href="/demo" style="color:#fff;margin-right:10px;text-decoration:none;">Live Status</a>
  <a href="/ads/manage" style="color:#fff;margin-right:10px;text-decoration:none;">Ads</a>
  <a href="/uptime" style="color:#fff;margin-right:10px;text-decoration:none;">Uptime</a>
  <a href="/logs" style="color:#fff;margin-right:10px;text-decoration:none;">Logs</a>
  <a href="/distributors" style="color:#fff;margin-right:10px;text-decoration:none;">Distributors</a>
  <a href="/docs" style="color:#fff;margin-right:10px;text-decoration:none;">API Docs</a>
  <a href="/logout" style="color:#fff;float:right;text-decoration:none;">Logout</a>
</nav>
"""


# ----------------- SAMPLE SETUP -----------------
@app.post("/api/setup-sample")
def setup_sample():
    db = next(get_db())

    # Check if any company exists
    company = db.query(Company).first()
    if company:
        return {"message": "Sample already exists", "company_id": company.id}

    # Create central company
    central_company = Company(name="Central Company")
    db.add(central_company)
    db.commit()
    db.refresh(central_company)

    # Create a screen under this company
    screen = Screen(name="Demo Screen 1", company_id=central_company.id, status="offline")
    db.add(screen)
    db.commit()
    db.refresh(screen)

    # Create two ads using sample videos (Vid1.mp4, Vid2.mp4 in /media)
    my_ad = Ad(
        name="Vid 1",
        owner_company_id=central_company.id,
        file_url="/media/Vid1.mp4",
        duration_sec=10,  # approximate
    )
    other_ad = Ad(
        name="Vid 2",
        owner_company_id=central_company.id + 999,  # pretend other company
        file_url="/media/Vid2.mp4",
        duration_sec=10,
    )
    db.add_all([my_ad, other_ad])
    db.commit()

    return {
        "message": "Sample data created",
        "company_id": central_company.id,
        "screen_id": screen.id,
        "my_ad_id": my_ad.id,
        "other_ad_id": other_ad.id,
    }


# ----------------- API: Screen Playlist -----------------
@app.get("/api/screens/{screen_id}/playlist")
def get_playlist(screen_id: int):
    db = next(get_db())

    # For now: SAME playlist for all screens = all ads
    ads = db.query(Ad).all()

    playlist = []
    for ad in ads:
        playlist.append({
            "id": ad.id,
            "name": ad.name,
            "owner_company_id": ad.owner_company_id,
            "file_url": ad.file_url,
            "duration_sec": ad.duration_sec,
        })

    return {
        "screen_id": screen_id,
        "playlist": playlist
    }


# ----------------- API: Heartbeat -----------------
@app.post("/api/events/heartbeat")
def heartbeat(hb: HeartbeatIn):
    db = next(get_db())
    screen = db.query(Screen).filter(Screen.id == hb.screen_id).first()
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    now_ts = datetime.utcnow()

    new_hb = Heartbeat(
        screen_id=hb.screen_id,
        timestamp=now_ts,
        current_ad_id=hb.current_ad_id,
        player_status=hb.player_status or "ok",
    )
    db.add(new_hb)

    # Update screen status
    screen.last_heartbeat_at = now_ts
    screen.status = "online"
    db.commit()

    return {"message": "heartbeat received", "timestamp": now_ts.isoformat()}


# ----------------- API: Playback Events -----------------
@app.post("/api/events/playback")
def playback_event(ev: PlaybackEventIn):
    db = next(get_db())
    screen = db.query(Screen).filter(Screen.id == ev.screen_id).first()
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    ad = db.query(Ad).filter(Ad.id == ev.ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    new_ev = PlaybackEvent(
        screen_id=ev.screen_id,
        ad_id=ev.ad_id,
        started_at=ev.started_at,
        ended_at=ev.ended_at,
        status=ev.status or "success",
    )
    db.add(new_ev)
    db.commit()

    return {"message": "playback recorded"}


# ----------------- API: Is my ad playing on this screen? -----------------
@app.get("/api/companies/{company_id}/screens/{screen_id}/current-ad")
def current_ad_for_company(company_id: int, screen_id: int):
    db = next(get_db())

    screen = db.query(Screen).filter(Screen.id == screen_id).first()
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    # latest heartbeat
    hb = (
        db.query(Heartbeat)
        .filter(Heartbeat.screen_id == screen_id)
        .order_by(Heartbeat.timestamp.desc())
        .first()
    )

    if not hb:
        return {
            "screen_id": screen_id,
            "online": False,
            "current_ad": None,
            "message": "No heartbeats yet",
        }

    # consider offline if last heartbeat > 120 seconds
    diff = datetime.utcnow() - hb.timestamp
    online = diff.total_seconds() <= 120

    if hb.current_ad_id is None:
        return {
            "screen_id": screen_id,
            "online": online,
            "current_ad": None,
            "message": "No ad currently reported",
        }

    ad = db.query(Ad).filter(Ad.id == hb.current_ad_id).first()
    if not ad:
        return {
            "screen_id": screen_id,
            "online": online,
            "current_ad": None,
            "message": "Unknown ad",
        }

    is_yours = (ad.owner_company_id == company_id)

    return {
        "screen_id": screen_id,
        "online": online,
        "current_ad": {
            "id": ad.id,
            "name": ad.name,
            "owner_company_id": ad.owner_company_id,
            "is_yours": is_yours,
        },
        "last_heartbeat_at": hb.timestamp.isoformat(),
    }


# ----------------- API: Basic metrics for an ad (today) -----------------
@app.get("/api/ads/{ad_id}/metrics/today")
def ad_metrics_today(ad_id: int):
    db = next(get_db())
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)

    plays_count = (
        db.query(PlaybackEvent)
        .filter(
            PlaybackEvent.ad_id == ad_id,
            PlaybackEvent.started_at >= start_of_day,
            PlaybackEvent.started_at < end_of_day,
        )
        .count()
    )

    return {
        "ad_id": ad_id,
        "name": ad.name,
        "plays_today": plays_count,
        "day_start_utc": start_of_day.isoformat(),
    }


# ----------------- Utility: list screens & ads -----------------
@app.get("/api/screens")
def list_screens():
    db = next(get_db())
    screens = db.query(Screen).all()
    out = []
    for s in screens:
        out.append({
            "id": s.id,
            "name": s.name,
            "company_id": s.company_id,
            "status": s.status,
            "last_heartbeat_at": s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None
        })
    return JSONResponse(out)


@app.get("/api/ads")
def list_ads():
    db = next(get_db())
    ads = db.query(Ad).all()
    out = []
    for a in ads:
        out.append({
            "id": a.id,
            "name": a.name,
            "owner_company_id": a.owner_company_id,
            "file_url": a.file_url,
            "duration_sec": a.duration_sec
        })
    return JSONResponse(out)


# ----------------- API: Upload Ads -----------------
@app.post("/api/ads/upload")
async def upload_ad(
    name: str = Form(...),
    owner_company_id: int = Form(1),
    file: UploadFile = File(...)
):
    """
    Upload a new ad file to /media and create an Ad record.
    """
    # Ensure media directory exists
    os.makedirs("media", exist_ok=True)

    # Generate a safe unique filename
    ext = os.path.splitext(file.filename)[1] or ".bin"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join("media", unique_name)

    # Save file to disk
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Store as relative URL
    file_url = f"/media/{unique_name}"

    db = next(get_db())

    ad = Ad(
        name=name,
        owner_company_id=owner_company_id,
        file_url=file_url,
        duration_sec=10,  # you can update/estimate later
    )
    db.add(ad)
    db.commit()
    db.refresh(ad)

    return {
        "message": "Ad uploaded",
        "ad": {
            "id": ad.id,
            "name": ad.name,
            "owner_company_id": ad.owner_company_id,
            "file_url": ad.file_url,
        }
    }


# ----------------- REAL PLAYER PAGE -----------------
@app.get("/player/screen/{screen_id}", response_class=HTMLResponse)
def player_page(screen_id: int):
    # Browser player that fetches playlist and loops ads
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Ad Player - Screen {screen_id}</title>
      <meta charset="utf-8" />
      <style>
        body, html {{
          margin: 0; padding: 0;
          background: black;
          overflow: hidden;
          height: 100%;
        }}
        #status {{
          position: absolute;
          top: 10px;
          left: 10px;
          z-index: 10;
          font-size: 14px;
          color: #fff;
          background: rgba(0,0,0,0.5);
          padding: 4px 8px;
          border-radius: 4px;
          font-family: Arial, sans-serif;
        }}
        video {{
          width: 100vw;
          height: 100vh;
          object-fit: cover;
          background: black;
        }}
      </style>
    </head>
    <body>
      <div id="status">Loading playlist...</div>
      <video id="video" autoplay muted playsinline></video>

      <script>
        const SCREEN_ID = {screen_id};
        const BASE_URL = window.location.origin;
        const video = document.getElementById("video");
        const statusEl = document.getElementById("status");
        let playlist = [];
        let index = 0;

        function setStatus(msg) {{
          if (statusEl) statusEl.textContent = msg;
          console.log(msg);
        }}

        async function fetchPlaylist() {{
          try {{
            const res = await fetch(BASE_URL + "/api/screens/" + SCREEN_ID + "/playlist");
            const data = await res.json();
            playlist = data.playlist || [];
            if (!playlist.length) {{
              setStatus("No ads found in playlist for screen " + SCREEN_ID);
            }} else {{
              setStatus("Playlist loaded (" + playlist.length + " ads) for screen " + SCREEN_ID);
            }}
          }} catch (err) {{
            setStatus("Error fetching playlist: " + err);
          }}
        }}

        async function sendHeartbeat(adId) {{
          fetch(BASE_URL + "/api/events/heartbeat", {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              screen_id: SCREEN_ID,
              current_ad_id: adId,
              player_status: 'ok'
            }})
          }}).catch(err => console.error("Heartbeat error", err));
        }}

        async function sendPlayback(adId, start, end) {{
          fetch(BASE_URL + "/api/events/playback", {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              screen_id: SCREEN_ID,
              ad_id: adId,
              started_at: start,
              ended_at: end,
              status: 'success'
            }})
          }}).catch(err => console.error("Playback error", err));
        }}

        function iso(d) {{ return d.toISOString(); }}

        async function playLoop() {{
          if (!playlist.length) {{
            await fetchPlaylist();
            if (!playlist.length) {{
              setStatus("No ads, retrying in 5s...");
              setTimeout(playLoop, 5000);
              return;
            }}
          }}

          const ad = playlist[index];
          index = (index + 1) % playlist.length;

          setStatus("Playing ad " + ad.id + ": " + ad.name);
          video.src = ad.file_url;

          const start = new Date();
          sendHeartbeat(ad.id);

          video.onended = () => {{
            const end = new Date();
            sendPlayback(ad.id, iso(start), iso(end));
            playLoop();
          }};

          video.onerror = () => {{
            setStatus("Error loading video " + ad.file_url + ", skipping...");
            playLoop();
          }};

          video.play().catch(err => {{
            setStatus("Autoplay blocked. Click the video to start.");
            console.error("Video play error", err);
          }});
        }}

        // User click: unmute and try to play with sound
        video.addEventListener('click', () => {{
          video.muted = false;
          video.play().then(() => {{
            setStatus("Playback started with sound.");
          }}).catch(err => {{
            setStatus("Unable to play: " + err);
          }});
        }});

        fetchPlaylist().then(playLoop);
      </script>
    </body>
    </html>
    """


# ----------------- AUTH & SIGNUP & DASHBOARD -----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EASTMAN</title>
        <meta charset="utf-8" />
        <style>
            body {
                margin: 0;
                background: radial-gradient(circle at top, #111827 0%, #020617 60%, #000 100%);
                color: #f9fafb;
                font-family: Arial, sans-serif;
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: hidden;
            }

            .splash-container {
                text-align: center;
                animation: fadeIn 1.2s ease forwards;
            }

            .splash-title {
                font-size: 68px;
                font-weight: 800;
                letter-spacing: 6px;
                margin-bottom: 12px;
            }

            .splash-subtitle {
                font-size: 20px;
                letter-spacing: 2px;
                color: #e5e7eb;
                opacity: 0.9;
            }

            @keyframes fadeIn {
                from {
                    opacity: 0;
                    transform: scale(0.95);
                }
                to {
                    opacity: 1;
                    transform: scale(1);
                }
            }

            @keyframes fadeOut {
                to {
                    opacity: 0;
                    transform: scale(1.05);
                }
            }
        </style>
    </head>
    <body>
        <div class="splash-container" id="splash">
            <div class="splash-title">EASTMAN</div>
            <div class="splash-subtitle">Material Innovation Company</div>
        </div>

        <script>
            // After 1.6 seconds, fade out and go to login/signup page
            setTimeout(() => {
                const splash = document.getElementById("splash");
                if (splash) {
                    splash.style.animation = "fadeOut 0.6s ease forwards";
                }

                setTimeout(() => {
                    window.location.href = "/auth";
                }, 550);

            }, 1600);
        </script>
    </body>
    </html>
    """
@app.get("/auth", response_class=HTMLResponse)
def auth_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EASTMAN - Ad Monitoring</title>
        <meta charset="utf-8" />
        <style>
            body {
                margin:0;
                background:#050814;
                color:white;
                font-family:Arial, sans-serif;
                display:flex;
                align-items:center;
                justify-content:center;
                height:100vh;
            }
            .center-box { text-align:center; }
            .title {
                font-size:60px;
                letter-spacing:4px;
                margin-bottom:10px;
            }
            .subtitle {
                color:#ccc;
                margin-bottom:30px;
                font-size:16px;
                letter-spacing:2px;
            }
            .btn {
                padding:12px 30px;
                margin:10px;
                background:#ffca28;
                border:none;
                font-size:16px;
                font-weight:bold;
                cursor:pointer;
                border-radius:6px;
            }
            .btn:hover {
                opacity:0.9;
            }
        </style>
    </head>
    <body>
        <div class="center-box">
            <div class="title">EASTMAN</div>
            <div class="subtitle">Ad Monitoring System</div>
            <button class="btn" onclick="location.href='/login'">LOGIN</button>
            <button class="btn" onclick="location.href='/signup'">SIGNUP</button>
        </div>
    </body>
    </html>
    """


@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>EASTMAN | Distributor Signup</title>
      <meta charset="utf-8" />
      <style>
        body {
          margin: 0;
          font-family: Arial, sans-serif;
          background: #0f1117;
          color: #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100vh;
        }

        .card {
          background: #161b22;
          padding: 28px 32px;
          border-radius: 12px;
          width: 380px;
          box-shadow: 0 0 20px rgba(0,0,0,0.6);
        }

        .brand {
          text-align: center;
          font-size: 32px;
          font-weight: bold;
          letter-spacing: 2px;
          margin-bottom: 6px;
          color: #00e5ff;
        }

        .subtitle {
          text-align: center;
          font-size: 13px;
          color: #aaa;
          margin-bottom: 22px;
        }

        label {
          display: block;
          margin-top: 12px;
          font-size: 13px;
          color: #ccc;
        }

        input, textarea {
          width: 100%;
          margin-top: 6px;
          padding: 10px;
          border-radius: 6px;
          border: 1px solid #2a2f3a;
          background: #0f1117;
          color: #fff;
          outline: none;
        }

        input:focus, textarea:focus {
          border-color: #00e5ff;
        }

        button {
          margin-top: 18px;
          width: 100%;
          padding: 12px;
          background: linear-gradient(90deg, #00e5ff, #00bcd4);
          color: #000;
          font-weight: bold;
          border: none;
          border-radius: 6px;
          cursor: pointer;
        }

        button:hover {
          opacity: 0.9;
        }

        .hint {
          margin-top: 14px;
          text-align: center;
          font-size: 12px;
          color: #aaa;
        }

        .hint a {
          color: #00e5ff;
          text-decoration: none;
        }
      </style>
    </head>

    <body>
      <div class="card">

        <div class="brand">EASTMAN</div>
        <div class="subtitle">Distributor Registration</div>

        <form method="post" action="/signup">

          <label>Full Name</label>
          <input type="text" name="full_name" required />

          <label>Company Name</label>
          <input type="text" name="company_name" required />

          <label>Address</label>
          <textarea name="address" rows="3" required></textarea>

          <label>Phone Number</label>
          <input type="text" name="phone" required />

          <label>Email</label>
          <input type="email" name="email" required />

          <label>Password</label>
          <input type="password" name="password" required />

          <button type="submit">Create Account</button>
        </form>

        <div class="hint">
          Already registered?
          <a href="/login">Log in</a>
        </div>

      </div>
    </body>
    </html>
    """


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    full_name: str = Form(...),
    company_name: str = Form(...),
    address: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)
):
    db = next(get_db())
    email = email.strip().lower()
    username = email  # we use email as username

    existing = db.query(Distributor).filter(Distributor.email == email).first()
    if existing:
        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html>
            <head>
              <title>Email Already Registered</title>
              <meta charset="utf-8" />
              <style>
                body {{
                  margin: 0;
                  font-family: Arial, sans-serif;
                  background: #050814;
                  color: #e5e5e5;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                }}
                .card {{
                  background: #111827;
                  padding: 24px 28px;
                  border-radius: 12px;
                  box-shadow: 0 0 20px rgba(0,0,0,0.4);
                  max-width: 380px;
                  text-align: center;
                }}
                a {{
                  color: #00e5ff;
                  text-decoration: none;
                }}
              </style>
            </head>
            <body>
              <div class="card">
                <h2>Email already registered</h2>
                <p>You can go back and <a href="/login">log in</a>.</p>
              </div>
            </body>
            </html>
            """,
            status_code=400,
        )

    # Create a Company for this distributor
    company = Company(name=company_name)
    db.add(company)
    db.commit()
    db.refresh(company)

    # Generate signup OTP
    otp = f"{random.randint(100000, 999999)}"
    otp_expires = datetime.utcnow() + timedelta(minutes=10)

    dist = Distributor(
        full_name=full_name,
        company_name=company_name,
        address=address,
        phone=phone,
        email=email,
        username=username,
        password=password,
        company_id=company.id,
        is_verified=False,
        otp_code=otp,
        otp_expires_at=otp_expires,
    )
    db.add(dist)
    db.commit()

    # Send OTP via "email"
    send_email(
        to_email=email,
        subject="Your Ad Monitor Signup OTP",
        body=f"Dear {full_name},\n\nYour signup OTP is: {otp}\nIt expires in 10 minutes.\n\n- Ad Monitor System"
    )

    # Dark OTP verify page
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Verify Email - EASTMAN</title>
      <meta charset="utf-8" />
      <style>
        body {{
          margin: 0;
          font-family: Arial, sans-serif;
          background:#050814;
          color:#e5e5e5;
          display:flex;
          align-items:center;
          justify-content:center;
          height:100vh;
        }}
        .card {{
          background:#111827;
          padding:24px 28px;
          border-radius:12px;
          box-shadow:0 0 20px rgba(0,0,0,0.45);
          width:360px;
        }}
        h1 {{
          font-size:20px;
          margin-bottom:10px;
        }}
        p {{
          font-size:13px;
          color:#cbd5f5;
        }}
        label {{
          display:block;
          margin-top:12px;
          font-size:13px;
          color:#e5e5e5;
        }}
        input {{
          width:100%;
          margin-top:6px;
          padding:10px;
          border-radius:6px;
          border:1px solid #374151;
          background:#020617;
          color:#e5e5e5;
          outline:none;
        }}
        input:focus {{
          border-color:#00e5ff;
        }}
        button {{
          margin-top:18px;
          width:100%;
          padding:10px;
          border-radius:6px;
          border:none;
          background:linear-gradient(90deg,#00e5ff,#00bcd4);
          color:#000;
          font-weight:bold;
          cursor:pointer;
        }}
        button:hover {{
          opacity:0.9;
        }}
        .hint {{
          font-size:11px;
          color:#9ca3af;
          margin-top:10px;
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Verify Email</h1>
        <p>We have sent an OTP to <b>{email}</b>.</p>
        <form method="post" action="/verify-otp">
          <input type="hidden" name="email" value="{email}" />
          <label>Enter OTP</label>
          <input type="text" name="otp" required />
          <button type="submit">Verify</button>
        </form>
        <div class="hint">
          For local testing, check the FastAPI server console to see the OTP that was "emailed".
        </div>
      </div>
    </body>
    </html>
    """



@app.post("/verify-otp", response_class=HTMLResponse)
def verify_otp(email: str = Form(...), otp: str = Form(...)):
    db = next(get_db())
    email = email.strip().lower()
    dist = db.query(Distributor).filter(Distributor.email == email).first()
    if not dist:
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
          <title>Unknown Email</title>
          <meta charset="utf-8" />
          <style>
            body {
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
            }
            .card {
              background: #111827;
              padding: 24px 28px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              max-width: 380px;
              text-align: center;
            }
            a { color: #00e5ff; text-decoration: none; }
          </style>
        </head>
        <body>
          <div class="card">
            <h2>Unknown email</h2>
            <p><a href="/signup">Sign up</a> again.</p>
          </div>
        </body>
        </html>
        """, status_code=400)

    if not dist.otp_code or not dist.otp_expires_at:
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
          <title>No OTP Pending</title>
          <meta charset="utf-8" />
          <style>
            body {
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
            }
            .card {
              background: #111827;
              padding: 24px 28px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              max-width: 380px;
              text-align: center;
            }
          </style>
        </head>
        <body>
          <div class="card">
            <h2>No OTP pending for this account.</h2>
          </div>
        </body>
        </html>
        """, status_code=400)

    if datetime.utcnow() > dist.otp_expires_at:
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
          <title>OTP Expired</title>
          <meta charset="utf-8" />
          <style>
            body {
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
            }
            .card {
              background: #111827;
              padding: 24px 28px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              max-width: 380px;
              text-align: center;
            }
            a { color: #00e5ff; text-decoration: none; }
          </style>
        </head>
        <body>
          <div class="card">
            <h2>OTP expired</h2>
            <p>Please <a href="/signup">sign up</a> again.</p>
          </div>
        </body>
        </html>
        """, status_code=400)

    if otp.strip() != dist.otp_code:
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
          <title>Invalid OTP</title>
          <meta charset="utf-8" />
          <style>
            body {
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
            }
            .card {
              background: #111827;
              padding: 24px 28px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              max-width: 380px;
              text-align: center;
            }
          </style>
        </head>
        <body>
          <div class="card">
            <h2>Invalid OTP</h2>
            <p>Please go back and try again.</p>
          </div>
        </body>
        </html>
        """, status_code=400)

    dist.is_verified = True
    dist.otp_code = None
    dist.otp_expires_at = None
    db.commit()

    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Email Verified - EASTMAN</title>
      <meta charset="utf-8" />
      <style>
        body {
          margin: 0;
          font-family: Arial, sans-serif;
          background: #050814;
          color: #e5e5e5;
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100vh;
        }
        .card {
          background: #111827;
          padding: 24px 28px;
          border-radius: 12px;
          box-shadow: 0 0 20px rgba(0,0,0,0.4);
          max-width: 380px;
          text-align: center;
        }
        a { color: #00e5ff; text-decoration: none; }
      </style>
    </head>
    <body>
      <div class="card">
        <h2>Email verified successfully</h2>
        <p>You can now <a href="/login">log in</a>.</p>
      </div>
    </body>
    </html>
    """
@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>EASTMAN | Login</title>
      <meta charset="utf-8" />
      <style>
        body {
          margin: 0;
          font-family: Arial, sans-serif;
          background: #0f1117;
          color: #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100vh;
        }

        .card {
          background: #161b22;
          padding: 28px 32px;
          border-radius: 12px;
          width: 360px;
          box-shadow: 0 0 20px rgba(0,0,0,0.6);
        }

        .brand {
          text-align: center;
          font-size: 32px;
          font-weight: bold;
          letter-spacing: 2px;
          margin-bottom: 6px;
          color: #00e5ff;
        }

        .subtitle {
          text-align: center;
          font-size: 13px;
          color: #aaa;
          margin-bottom: 22px;
        }

        label {
          display: block;
          margin-top: 12px;
          font-size: 13px;
          color: #ccc;
        }

        input {
          width: 100%;
          margin-top: 6px;
          padding: 10px;
          border-radius: 6px;
          border: 1px solid #2a2f3a;
          background: #0f1117;
          color: #fff;
          outline: none;
        }

        input:focus {
          border-color: #00e5ff;
        }

        button {
          margin-top: 18px;
          width: 100%;
          padding: 12px;
          background: linear-gradient(90deg, #00e5ff, #00bcd4);
          color: #000;
          font-weight: bold;
          border: none;
          border-radius: 6px;
          cursor: pointer;
        }

        button:hover {
          opacity: 0.9;
        }

        .hint {
          margin-top: 14px;
          text-align: center;
          font-size: 12px;
          color: #aaa;
        }

        .hint a {
          color: #00e5ff;
          text-decoration: none;
        }

        .demo {
          margin-top: 10px;
          text-align: center;
          font-size: 11px;
          color: #888;
        }
      </style>
    </head>

    <body>
      <div class="card">

        <div class="brand">EASTMAN</div>
        <div class="subtitle">Secure Login</div>

        <form method="post" action="/login">

          <label>Username / Email</label>
          <input type="text" name="username" required />

          <label>Password</label>
          <input type="password" name="password" required />

          <button type="submit">Login</button>
        </form>

        <div class="hint">
          New distributor?
          <a href="/signup">Create account</a>
        </div>

        <div class="demo">
          Master Login: master / master123
        </div>

      </div>
    </body>
    </html>
    """

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    db = next(get_db())
    username = username.strip()

    # Master login (no 2FA)
    if username == "master":
        user = USERS.get("master")
        if not user or user["password"] != password:
            html = """
            <!DOCTYPE html>
            <html>
            <head>
              <title>Invalid Master Login</title>
              <meta charset="utf-8" />
              <style>
                body {
                  margin: 0;
                  font-family: Arial, sans-serif;
                  background: #050814;
                  color: #e5e5e5;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                }
                .card {
                  background: #111827;
                  padding: 24px 28px;
                  border-radius: 12px;
                  box-shadow: 0 0 20px rgba(0,0,0,0.4);
                  max-width: 380px;
                  text-align: center;
                }
                a { color: #00e5ff; text-decoration: none; }
              </style>
            </head>
            <body>
              <div class="card">
                <h2>Invalid master credentials</h2>
                <p><a href="/login">Try again</a></p>
              </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html, status_code=401)

        resp = RedirectResponse("/dashboard", status_code=303)
        resp.set_cookie("username", "master")
        resp.set_cookie("role", "master")
        return resp

    # Distributor login with 2FA
    email = username.strip().lower()
    dist = db.query(Distributor).filter(Distributor.email == email).first()
    if not dist or dist.password != password:
        html = """
        <!DOCTYPE html>
        <html>
        <head>
          <title>Invalid Login</title>
          <meta charset="utf-8" />
          <style>
            body {
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
            }
            .card {
              background: #111827;
              padding: 24px 28px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              max-width: 380px;
              text-align: center;
            }
            a { color: #00e5ff; text-decoration: none; }
          </style>
        </head>
        <body>
          <div class="card">
            <h2>Invalid email / password</h2>
            <p><a href="/login">Try again</a></p>
          </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=401)

    if not dist.is_verified:
        return HTMLResponse(
            """
            <!DOCTYPE html>
            <html>
            <head>
              <title>Email Not Verified</title>
              <meta charset="utf-8" />
              <style>
                body {
                  margin: 0;
                  font-family: Arial, sans-serif;
                  background: #050814;
                  color: #e5e5e5;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                }
                .card {
                  background: #111827;
                  padding: 24px 28px;
                  border-radius: 12px;
                  box-shadow: 0 0 20px rgba(0,0,0,0.4);
                  max-width: 380px;
                  text-align: center;
                }
              </style>
            </head>
            <body>
              <div class="card">
                <h2>Your email is not verified yet.</h2>
                <p>Please complete OTP verification first.</p>
              </div>
            </body>
            </html>
            """,
            status_code=403,
        )

    # Generate login OTP
    otp = f"{random.randint(100000, 999999)}"
    dist.login_otp = otp
    dist.login_otp_expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.commit()

    send_email(
        to_email=dist.email,
        subject="Your Ad Monitor Login OTP",
        body=f"Dear {dist.full_name},\n\nYour login OTP is: {otp}\nIt expires in 10 minutes.\n\n- Ad Monitor System"
    )

    # Dark login-OTP page
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Login OTP - EASTMAN</title>
      <meta charset="utf-8" />
      <style>
        body {{
          margin: 0;
          font-family: Arial, sans-serif;
          background:#050814;
          color:#e5e5e5;
          display:flex;
          align-items:center;
          justify-content:center;
          height:100vh;
        }}
        .card {{
          background:#111827;
          padding:24px 28px;
          border-radius:12px;
          box-shadow:0 0 20px rgba(0,0,0,0.45);
          width:360px;
        }}
        h1 {{
          font-size:20px;
          margin-bottom:10px;
        }}
        p {{
          font-size:13px;
          color:#cbd5f5;
        }}
        label {{
          display:block;
          margin-top:12px;
          font-size:13px;
          color:#e5e5e5;
        }}
        input {{
          width:100%;
          margin-top:6px;
          padding:10px;
          border-radius:6px;
          border:1px solid #374151;
          background:#020617;
          color:#e5e5e5;
          outline:none;
        }}
        input:focus {{
          border-color:#00e5ff;
        }}
        button {{
          margin-top:18px;
          width:100%;
          padding:10px;
          border-radius:6px;
          border:none;
          background:linear-gradient(90deg,#00e5ff,#00bcd4);
          color:#000;
          font-weight:bold;
          cursor:pointer;
        }}
        button:hover {{
          opacity:0.9;
        }}
        .hint {{
          font-size:11px;
          color:#9ca3af;
          margin-top:10px;
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Enter Login OTP</h1>
        <p>We sent a login OTP to <b>{dist.email}</b>.</p>
        <form method="post" action="/login-otp">
          <input type="hidden" name="email" value="{dist.email}" />
          <label>OTP</label>
          <input type="text" name="otp" required />
          <button type="submit">Verify & Login</button>
        </form>
        <div class="hint">
          For local testing, check the FastAPI server console to see the OTP.
        </div>
      </div>
    </body>
    </html>
    """)

@app.post("/login-otp")
def login_otp(email: str = Form(...), otp: str = Form(...)):
    db = next(get_db())
    email = email.strip().lower()
    dist = db.query(Distributor).filter(Distributor.email == email).first()
    if not dist:
        return HTMLResponse("<p>Unknown email.</p>", status_code=400)

    if not dist.login_otp or not dist.login_otp_expires_at:
        return HTMLResponse("<p>No login OTP requested for this account.</p>", status_code=400)

    if datetime.utcnow() > dist.login_otp_expires_at:
        return HTMLResponse("<p>Login OTP expired. Please log in again.</p>", status_code=400)

    if otp.strip() != dist.login_otp:
        return HTMLResponse("<p>Invalid OTP. Please try again.</p>", status_code=400)

    # success
    dist.login_otp = None
    dist.login_otp_expires_at = None
    dist.last_login_at = datetime.utcnow()
    db.commit()

    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie("username", dist.username)
    resp.set_cookie("role", "distributor")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("username")
    resp.delete_cookie("role")
    return resp


# ----------------- DASHBOARD (MASTER vs DISTRIBUTOR) -----------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    username = user["username"]
    role = user["role"]
    db = next(get_db())

    # ---------- MASTER VIEW ----------
    if role == "master":
        now = datetime.utcnow()
        start_of_day = datetime(now.year, now.month, now.day)
        end_of_day = start_of_day + timedelta(days=1)
        start_of_month = datetime(now.year, now.month, 1)
        end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)

        companies = db.query(Company).all()
        screens = db.query(Screen).all()

        # Group screens by company
        screens_by_company = {}
        for s in screens:
            screens_by_company.setdefault(s.company_id, []).append(s)

        rows_html = ""
        labels = []
        play_today_vals = []
        uptime_vals = []

        for c in companies:
            comp_screens = screens_by_company.get(c.id, [])
            if not comp_screens:
                continue

            screen_ids = [s.id for s in comp_screens]

            # Total play time today (seconds)
            ev_today = (
                db.query(PlaybackEvent)
                .filter(
                    PlaybackEvent.screen_id.in_(screen_ids),
                    PlaybackEvent.started_at >= start_of_day,
                    PlaybackEvent.started_at < end_of_day,
                )
                .all()
            )
            total_secs_today = sum(
                (e.ended_at - e.started_at).total_seconds()
                for e in ev_today
                if e.started_at and e.ended_at
            )

            # Total play time this month (seconds)
            ev_month = (
                db.query(PlaybackEvent)
                .filter(
                    PlaybackEvent.screen_id.in_(screen_ids),
                    PlaybackEvent.started_at >= start_of_month,
                    PlaybackEvent.started_at < end_of_month,
                )
                .all()
            )
            total_secs_month = sum(
                (e.ended_at - e.started_at).total_seconds()
                for e in ev_month
                if e.started_at and e.ended_at
            )

            # Approx uptime per screen (last 60 min), average across screens
            window_minutes = 60
            window_start = now - timedelta(minutes=window_minutes)
            screen_uptimes = []
            for s in comp_screens:
                hb_first = (
                    db.query(Heartbeat)
                    .filter(
                        Heartbeat.screen_id == s.id,
                        Heartbeat.timestamp >= window_start,
                        Heartbeat.timestamp <= now,
                    )
                    .order_by(Heartbeat.timestamp.asc())
                    .first()
                )
                hb_last = (
                    db.query(Heartbeat)
                    .filter(
                        Heartbeat.screen_id == s.id,
                        Heartbeat.timestamp >= window_start,
                        Heartbeat.timestamp <= now,
                    )
                    .order_by(Heartbeat.timestamp.desc())
                    .first()
                )
                if not hb_first or not hb_last:
                    screen_uptimes.append(0.0)
                else:
                    observed = (hb_last.timestamp - hb_first.timestamp).total_seconds()
                    total = window_minutes * 60
                    up = max(0.0, min(100.0, (observed / total) * 100.0))
                    screen_uptimes.append(up)

            avg_uptime = round(sum(screen_uptimes) / len(screen_uptimes), 2) if screen_uptimes else 0.0

            rows_html += f"""
              <tr>
                <td>{c.id}</td>
                <td>{c.name}</td>
                <td>{len(comp_screens)}</td>
                <td>{total_secs_today/60:.1f} min</td>
                <td>{total_secs_month/60:.1f} min</td>
                <td>{avg_uptime}%</td>
              </tr>
            """

            labels.append(c.name)
            play_today_vals.append(total_secs_today / 60.0)
            uptime_vals.append(avg_uptime)

        labels_js = ",".join(f'"{name}"' for name in labels)
        plays_today_js = ",".join(f"{v:.1f}" for v in play_today_vals)
        uptime_js = ",".join(f"{v:.2f}" for v in uptime_vals)

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
          <title>Master Dashboard - Ad Monitor</title>
          <meta charset="utf-8" />
          <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
          <style>
            body {{
              margin: 0;
              font-family: Arial, sans-serif;
              background: #050814;
              color: #e5e5e5;
            }}
            .page {{
              display: flex;
              justify-content: center;
              padding: 24px 16px;
            }}
            .container {{
              width: 100%;
              max-width: 1100px;
            }}
            .card {{
              background: #111827;
              padding: 16px 20px;
              border-radius: 12px;
              box-shadow: 0 0 20px rgba(0,0,0,0.4);
              margin-bottom: 18px;
            }}
            .title {{
              font-size: 20px;
              margin-bottom: 8px;
            }}
            .navspacer {{
              height: 8px;
            }}
            table {{
              border-collapse: collapse;
              width: 100%;
              margin-top: 10px;
              font-size: 13px;
            }}
            th, td {{
              border: 1px solid #1f2933;
              padding: 6px 8px;
            }}
            th {{
              background: #111827;
              color: #e5e5e5;
            }}
            tr:nth-child(even) td {{
              background: #020617;
            }}
            tr:nth-child(odd) td {{
              background: #020617;
            }}
            a.button-link {{
              display: inline-block;
              margin: 4px 4px 0 0;
              padding: 6px 10px;
              background: linear-gradient(90deg, #00e5ff, #00bcd4);
              color: #000;
              border-radius: 4px;
              text-decoration: none;
              font-size: 13px;
              font-weight: bold;
            }}
            a.button-link:hover {{
              opacity: 0.9;
            }}
            .charts-row {{
              display: flex;
              flex-wrap: wrap;
              gap: 16px;
            }}
            .chart-card {{
              flex: 1;
              min-width: 260px;
            }}
          </style>
        </head>
        <body>
          {NAV_HTML}
          <div class="navspacer"></div>
          <div class="page">
            <div class="container">
              <div class="card">
                <div class="title">Welcome, {username} (Master)</div>
                <p>Central view of all distributors and their performance.</p>
                <p>
                  <a class="button-link" href="/uptime">Open Uptime Page</a>
                  <a class="button-link" href="/logs">Open Logs Viewer</a>
                  <a class="button-link" href="/ads/manage">Manage Ads</a>
                </p>
              </div>

              <div class="card charts-row">
                <div class="chart-card">
                  <h3>Play Time Today (minutes)</h3>
                  <canvas id="playTimeChart" height="180"></canvas>
                </div>
                <div class="chart-card">
                  <h3>Avg Uptime (last 60 min)</h3>
                  <canvas id="uptimeChart" height="180"></canvas>
                </div>
              </div>

              <div class="card">
                <div class="title">Distributors Overview (by Company)</div>
                <table>
                  <thead>
                    <tr>
                      <th>Distributor/Company ID</th>
                      <th>Name</th>
                      <th># Screens</th>
                      <th>Play Time Today</th>
                      <th>Play Time This Month</th>
                      <th>Avg Uptime (last 60 min)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows_html or "<tr><td colspan='6'>No distributors with screens yet.</td></tr>"}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <script>
            const labels = [{labels_js}];
            const playToday = [{plays_today_js}];
            const uptime = [{uptime_js}];

            if (labels.length > 0) {{
              const ctx1 = document.getElementById('playTimeChart').getContext('2d');
              new Chart(ctx1, {{
                type: 'bar',
                data: {{
                  labels: labels,
                  datasets: [{{
                    label: 'Minutes played today',
                    data: playToday
                  }}]
                }},
                options: {{
                  responsive: true,
                  scales: {{
                    y: {{ beginAtZero: true }}
                  }}
                }}
              }});

              const ctx2 = document.getElementById('uptimeChart').getContext('2d');
              new Chart(ctx2, {{
                type: 'bar',
                data: {{
                  labels: labels,
                  datasets: [{{
                    label: 'Avg uptime %',
                    data: uptime
                  }}]
                }},
                options: {{
                  responsive: true,
                  scales: {{
                    y: {{ beginAtZero: true, max: 100 }}
                  }}
                }}
              }});
            }} else {{
              const c1 = document.getElementById('playTimeChart');
              const c2 = document.getElementById('uptimeChart');
              if (c1) c1.outerHTML = "<p>No distributor data yet.</p>";
              if (c2) c2.outerHTML = "<p>No distributor data yet.</p>";
            }}
          </script>
        </body>
        </html>
        """

    # ---------- DISTRIBUTOR VIEW ----------
    db_dist = db.query(Distributor).filter(Distributor.username == username).first()
    if not db_dist:
        return HTMLResponse("<p>Unknown distributor user.</p>", status_code=400)

    company_id = db_dist.company_id
    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)
    start_of_month = datetime(now.year, now.month, 1)
    end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)

    screens = db.query(Screen).filter(Screen.company_id == company_id).all()

    current_html = ""
    if screens:
        s = screens[0]
        hb = (
            db.query(Heartbeat)
            .filter(Heartbeat.screen_id == s.id)
            .order_by(Heartbeat.timestamp.desc())
            .first()
        )
        if hb:
            diff = datetime.utcnow() - hb.timestamp
            online = diff.total_seconds() <= 120
            if hb.current_ad_id:
                ad = db.query(Ad).filter(Ad.id == hb.current_ad_id).first()
                ad_name = ad.name if ad else "Unknown"
            else:
                ad_name = "None"

            current_html = f"""
              <p><b>Screen:</b> {s.name} (#{s.id})</p>
              <p><b>Online:</b> {"Yes" if online else "No"}</p>
              <p><b>Current Ad:</b> {ad_name}</p>
              <p><b>Last heartbeat:</b> {hb.timestamp.isoformat()}</p>
            """
        else:
            current_html = f"<p>No heartbeats yet for screen {s.name} (#{s.id}).</p>"
    else:
        current_html = "<p>You have no screens assigned yet.</p>"

    ads = db.query(Ad).filter(Ad.owner_company_id == company_id).all()
    ads_rows = ""
    ad_labels = []
    ad_plays_today = []
    ad_plays_month = []

    for ad in ads:
        plays_today = (
            db.query(PlaybackEvent)
            .filter(
                PlaybackEvent.ad_id == ad.id,
                PlaybackEvent.started_at >= start_of_day,
                PlaybackEvent.started_at < end_of_day,
            )
            .count()
        )
        plays_month = (
            db.query(PlaybackEvent)
            .filter(
                PlaybackEvent.ad_id == ad.id,
                PlaybackEvent.started_at >= start_of_month,
                PlaybackEvent.started_at < end_of_month,
            )
            .count()
        )
        ads_rows += f"""
          <tr>
            <td>{ad.id}</td>
            <td>{ad.name}</td>
            <td>{plays_today}</td>
            <td>{plays_month}</td>
          </tr>
        """
        ad_labels.append(ad.name)
        ad_plays_today.append(plays_today)
        ad_plays_month.append(plays_month)

    ad_labels_js = ",".join(f'"{n}"' for n in ad_labels)
    ad_today_js = ",".join(str(v) for v in ad_plays_today)
    ad_month_js = ",".join(str(v) for v in ad_plays_month)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Distributor Dashboard - Ad Monitor</title>
      <meta charset="utf-8" />
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>
        body {{
          margin: 0;
          font-family: Arial, sans-serif;
          background:#050814;
          color:#e5e5e5;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:900px;
        }}
        .card {{
          background:#111827;
          padding:16px 20px;
          border-radius:12px;
          box-shadow:0 0 20px rgba(0,0,0,0.4);
          margin-bottom:18px;
        }}
        .title {{
          font-size:20px;
          margin-bottom:8px;
        }}
        .navspacer {{
          height:8px;
        }}
        table {{
          border-collapse: collapse;
          width: 100%;
          margin-top:10px;
          font-size:13px;
        }}
        th, td {{
          border: 1px solid #1f2933;
          padding: 6px 8px;
        }}
        th {{
          background:#111827;
          color:#e5e5e5;
        }}
        tr:nth-child(even) td {{
          background:#020617;
        }}
        tr:nth-child(odd) td {{
          background:#020617;
        }}
        input[type="number"] {{
          padding:8px 10px;
          border-radius:6px;
          border:1px solid #374151;
          background:#020617;
          color:#e5e5e5;
          margin-right:8px;
          width:160px;
        }}
        button.create-btn {{
          padding:8px 14px;
          border-radius:6px;
          border:none;
          background: linear-gradient(90deg,#00e5ff,#00bcd4);
          color:#000;
          font-weight:bold;
          cursor:pointer;
        }}
        button.create-btn:hover {{
          opacity:0.9;
        }}
        .status-text {{
          margin-top:8px;
          font-size:13px;
          color:#9ca3af;
        }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div class="navspacer"></div>
      <div class="page">
        <div class="container">
          <div class="card">
            <div class="title">Welcome, {db_dist.full_name} (Distributor)</div>
            <p>Company: {db_dist.company_name}</p>
          </div>

          <div class="card">
            <div class="title">Create Screens</div>
            <p>Enter how many screens you want to create for your company.</p>
            <input type="number" id="screenCount" placeholder="Number of screens" min="1" />
            <button class="create-btn" onclick="createScreens()">Create</button>
            <div id="screenStatus" class="status-text"></div>
          </div>

          <div class="card">
            <div class="title">Current Ad (First Screen)</div>
            {current_html}
          </div>

          <div class="card">
            <div class="title">Your Ads – Plays</div>
            <canvas id="adsChart" height="180"></canvas>
            <table>
              <thead>
                <tr>
                  <th>Ad ID</th>
                  <th>Ad Name</th>
                  <th>Plays Today</th>
                  <th>Plays This Month</th>
                </tr>
              </thead>
              <tbody>
                {ads_rows or "<tr><td colspan='4'>No ads assigned yet.</td></tr>"}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        const COMPANY_ID = {company_id};

        const adLabels = [{ad_labels_js}];
        const adToday = [{ad_today_js}];
        const adMonth = [{ad_month_js}];

        if (adLabels.length > 0) {{
          const ctx = document.getElementById('adsChart').getContext('2d');
          new Chart(ctx, {{
            type: 'bar',
            data: {{
              labels: adLabels,
              datasets: [
                {{
                  label: 'Plays Today',
                  data: adToday
                }},
                {{
                  label: 'Plays This Month',
                  data: adMonth
                }}
              ]
            }},
            options: {{
              responsive: true,
              scales: {{
                y: {{ beginAtZero: true }}
              }}
            }}
          }});
        }} else {{
          const c = document.getElementById('adsChart');
          if (c) c.outerHTML = "<p>No ads to chart yet.</p>";
        }}

        async function createScreens() {{
          const countInput = document.getElementById("screenCount");
          const statusEl = document.getElementById("screenStatus");
          const raw = countInput.value.trim();
          const count = parseInt(raw || "0", 10);

          if (!count || count <= 0) {{
            statusEl.textContent = "Please enter a valid number of screens.";
            return;
          }}

          statusEl.textContent = "Creating screens...";

          const formData = new FormData();
          formData.append("count", count);
          formData.append("company_id", COMPANY_ID);

          try {{
            const res = await fetch("/api/screens/create-bulk", {{
              method: "POST",
              body: formData
            }});
            const data = await res.json();
            statusEl.textContent = data.message || "Screens created.";
          }} catch (err) {{
            console.error(err);
            statusEl.textContent = "Failed to create screens. Please try again.";
          }}
        }}
      </script>
    </body>
    </html>
    """

    # ---------- DISTRIBUTOR VIEW ----------
    db_dist = db.query(Distributor).filter(Distributor.username == username).first()
    if not db_dist:
        return HTMLResponse("<p>Unknown distributor user.</p>", status_code=400)

    company_id = db_dist.company_id
    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)
    start_of_month = datetime(now.year, now.month, 1)
    end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)

    screens = db.query(Screen).filter(Screen.company_id == company_id).all()

    current_html = ""
    if screens:
        s = screens[0]
        hb = (
            db.query(Heartbeat)
            .filter(Heartbeat.screen_id == s.id)
            .order_by(Heartbeat.timestamp.desc())
            .first()
        )
        if hb:
            diff = datetime.utcnow() - hb.timestamp
            online = diff.total_seconds() <= 120
            if hb.current_ad_id:
                ad = db.query(Ad).filter(Ad.id == hb.current_ad_id).first()
                ad_name = ad.name if ad else "Unknown"
            else:
                ad_name = "None"

            current_html = f"""
              <p><b>Screen:</b> {s.name} (#{s.id})</p>
              <p><b>Online:</b> {"Yes" if online else "No"}</p>
              <p><b>Current Ad:</b> {ad_name}</p>
              <p><b>Last heartbeat:</b> {hb.timestamp.isoformat()}</p>
            """
        else:
            current_html = f"<p>No heartbeats yet for screen {s.name} (#{s.id}).</p>"
    else:
        current_html = "<p>You have no screens assigned yet.</p>"

    ads = db.query(Ad).filter(Ad.owner_company_id == company_id).all()
    ads_rows = ""
    ad_labels = []
    ad_plays_today = []
    ad_plays_month = []

    for ad in ads:
        plays_today = (
            db.query(PlaybackEvent)
            .filter(
                PlaybackEvent.ad_id == ad.id,
                PlaybackEvent.started_at >= start_of_day,
                PlaybackEvent.started_at < end_of_day,
            )
            .count()
        )
        plays_month = (
            db.query(PlaybackEvent)
            .filter(
                PlaybackEvent.ad_id == ad.id,
                PlaybackEvent.started_at >= start_of_month,
                PlaybackEvent.started_at < end_of_month,
            )
            .count()
        )
        ads_rows += f"""
          <tr>
            <td>{ad.id}</td>
            <td>{ad.name}</td>
            <td>{plays_today}</td>
            <td>{plays_month}</td>
          </tr>
        """
        ad_labels.append(ad.name)
        ad_plays_today.append(plays_today)
        ad_plays_month.append(plays_month)

    ad_labels_js = ",".join(f'"{n}"' for n in ad_labels)
    ad_today_js = ",".join(str(v) for v in ad_plays_today)
    ad_month_js = ",".join(str(v) for v in ad_plays_month)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Distributor Dashboard - EASTMAN</title>
      <meta charset="utf-8" />
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>
        body {{
          margin: 0;
          font-family: Arial, sans-serif;
          background: #0f1117;
          color: #ffffff;
        }}
        .navspacer {{ height: 8px; }}
        .outer-container {{
          min-height: calc(100vh - 50px);
          display: flex;
          justify-content: center;
          align-items: flex-start;
        }}
        .container {{
          width: 100%;
          max-width: 900px;
          padding: 20px;
        }}
        .card {{
          background: #161b22;
          color: #ffffff;
          padding: 16px;
          border-radius: 8px;
          box-shadow: 0 0 20px rgba(0,0,0,0.5);
          margin-bottom: 16px;
        }}
        .title {{ font-size: 20px; margin-bottom: 8px; }}
        table {{
          border-collapse: collapse;
          width: 100%;
          margin-top: 10px;
          font-size: 13px;
        }}
        th, td {{
          border: 1px solid #2a2f3a;
          padding: 6px 8px;
        }}
        th {{
          background: #111520;
        }}
        input[type="number"] {{
          padding: 8px;
          border-radius: 6px;
          border: 1px solid #2a2f3a;
          background: #0f1117;
          color: #fff;
          outline: none;
          margin-right: 8px;
        }}
        input[type="number"]:focus {{
          border-color: #00e5ff;
        }}
        button {{
          padding: 8px 14px;
          border-radius: 6px;
          border: none;
          cursor: pointer;
          background: linear-gradient(90deg, #00e5ff, #00bcd4);
          color: #000;
          font-weight: bold;
        }}
        button:hover {{
          opacity: 0.9;
        }}
        #screenStatus {{
          margin-top: 8px;
          font-size: 13px;
          color: #00e5ff;
        }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div class="navspacer"></div>
      <div class="outer-container">
        <div class="container">
          <div class="card">
            <div class="title">Welcome, {db_dist.full_name} (Distributor)</div>
            <p>Company: {db_dist.company_name}</p>
          </div>

          <div class="card">
            <div class="title">Create Screens</div>
            <input type="number" id="screenCount" placeholder="Enter number of screens" min="1" />
            <button id="createScreensBtn">Create</button>
            <div id="screenStatus"></div>
          </div>

          <div class="card">
            <div class="title">Current Ad (First Screen)</div>
            {current_html}
          </div>

          <div class="card">
            <div class="title">Your Ads – Plays</div>
            <canvas id="adsChart" height="180"></canvas>
            <table>
              <thead>
                <tr>
                  <th>Ad ID</th>
                  <th>Ad Name</th>
                  <th>Plays Today</th>
                  <th>Plays This Month</th>
                </tr>
              </thead>
              <tbody>
                {ads_rows or "<tr><td colspan='4'>No ads assigned yet.</td></tr>"}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        const adLabels = [{ad_labels_js}];
        const adToday = [{ad_today_js}];
        const adMonth = [{ad_month_js}];

        if (adLabels.length > 0) {{
          const ctx = document.getElementById('adsChart').getContext('2d');
          new Chart(ctx, {{
            type: 'bar',
            data: {{
              labels: adLabels,
              datasets: [
                {{
                  label: 'Plays Today',
                  data: adToday
                }},
                {{
                  label: 'Plays This Month',
                  data: adMonth
                }}
              ]
            }},
            options: {{
              responsive: true,
              scales: {{
                y: {{ beginAtZero: true }}
              }}
            }}
          }});
        }} else {{
          const c = document.getElementById('adsChart');
          if (c) c.outerHTML = "<p>No ads to chart yet.</p>";
        }}

        const COMPANY_ID = {company_id};
        const createBtn = document.getElementById("createScreensBtn");

        if (createBtn) {{
          createBtn.addEventListener("click", async () => {{
            const input = document.getElementById("screenCount");
            const statusEl = document.getElementById("screenStatus");
            const countVal = parseInt((input.value || "0"), 10);

            if (!countVal || countVal <= 0) {{
              if (statusEl) statusEl.textContent = "Please enter a valid number of screens.";
              return;
            }}

            const formData = new FormData();
            formData.append("count", countVal);
            formData.append("company_id", COMPANY_ID);

            try {{
              const res = await fetch("/api/screens/create-bulk", {{
                method: "POST",
                body: formData
              }});
              const data = await res.json();
              if (statusEl) statusEl.textContent = data.message || "Screens created.";
            }} catch (err) {{
              if (statusEl) statusEl.textContent = "Error creating screens.";
              console.error(err);
            }}
          }});
        }}
      </script>
    </body>
    </html>
    """



# ----------------- MASTER-ONLY: DISTRIBUTORS LIST -----------------
@app.get("/distributors", response_class=HTMLResponse)
def distributors_page(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "master":
        return HTMLResponse("<p>Access denied.</p>", status_code=403)

    db = next(get_db())
    dists = db.query(Distributor).all()

    rows = ""
    for d in dists:
        rows += f"""
        <tr>
          <td>{d.id}</td>
          <td>{d.full_name}</td>
          <td>{d.company_name}</td>
          <td>{d.email}</td>
          <td>{d.phone}</td>
          <td>{d.address}</td>
          <td>{"Yes" if d.is_verified else "No"}</td>
          <td>{d.created_at.isoformat() if d.created_at else ""}</td>
          <td>{d.last_login_at.isoformat() if d.last_login_at else ""}</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Distributors - Master View</title>
      <meta charset="utf-8" />
      <style>
        body {{
          font-family: Arial, sans-serif;
          background:#050814;
          color:#e5e5e5;
          margin:0;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:1200px;
        }}
        .card {{
          background:#111827;
          padding:16px 20px;
          border-radius:12px;
          box-shadow:0 0 20px rgba(0,0,0,0.4);
          margin-bottom:18px;
        }}
        table {{
          border-collapse: collapse;
          width: 100%;
          margin-top:10px;
          font-size:12px;
        }}
        th, td {{
          border: 1px solid #1f2933;
          padding: 4px 6px;
        }}
        th {{
          background:#111827;
          color:#e5e5e5;
        }}
        tr:nth-child(even) td {{
          background:#020617;
        }}
        tr:nth-child(odd) td {{
          background:#020617;
        }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div style="height:8px;"></div>
      <div class="page">
        <div class="container">
          <div class="card">
            <h1>All Distributors (Master Only)</h1>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Full Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Address</th>
                  <th>Verified</th>
                  <th>Created At</th>
                  <th>Last Login</th>
                </tr>
              </thead>
              <tbody>
                {rows or "<tr><td colspan='9'>No distributors yet.</td></tr>"}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </body>
    </html>
    """


# ----------------- SIMPLE DEMO LIVE STATUS PAGE -----------------
@app.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Ad Monitoring Demo</title>
      <meta charset="utf-8" />
      <style>
        body {{
          font-family: Arial, sans-serif;
          padding: 0;
          margin:0;
          background: #050814;
          color:#e5e5e5;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:900px;
        }}
        .card {{
          background: #111827;
          border-radius: 12px;
          padding: 16px 20px;
          margin-bottom: 18px;
          box-shadow: 0 0 20px rgba(0,0,0,0.4);
        }}
        .label {{ font-weight: bold; }}
        .ok {{ color: #34d399; font-weight: bold; }}
        .bad {{ color: #f97373; font-weight: bold; }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div style="height:8px;"></div>

      <div class="page">
        <div class="container">
          <h1>Ad Monitoring Demo (Screen 1)</h1>

          <div class="card">
            <h2>Live Screen Status</h2>
            <p><span class="label">Online:</span> <span id="online">...</span></p>
            <p><span class="label">Current ad:</span> <span id="current_ad">...</span></p>
            <p><span class="label">Is yours:</span> <span id="is_yours">...</span></p>
            <p><span class="label">Last heartbeat:</span> <span id="heartbeat">...</span></p>
          </div>

          <div class="card">
            <h2>"My Ad" Metrics (Ad 1)</h2>
            <p><span class="label">Plays today:</span> <span id="plays_today">...</span></p>
          </div>
        </div>
      </div>

      <script>
        async function refresh() {{
          const currentRes = await fetch('/api/companies/1/screens/1/current-ad');
          const current = await currentRes.json();

          const onlineEl = document.getElementById('online');
          onlineEl.textContent = current.online ? 'Yes' : 'No';
          onlineEl.className = current.online ? 'ok' : 'bad';

          if (current.current_ad) {{
            document.getElementById('current_ad').textContent = current.current_ad.name;
            const isYoursEl = document.getElementById('is_yours');
            isYoursEl.textContent = current.current_ad.is_yours ? 'Yes' : 'No';
            isYoursEl.className = current.current_ad.is_yours ? 'ok' : 'bad';
          }} else {{
            document.getElementById('current_ad').textContent = 'None';
            document.getElementById('is_yours').textContent = '-';
          }}

          document.getElementById('heartbeat').textContent = current.last_heartbeat_at || 'N/A';

          const metricsRes = await fetch('/api/ads/1/metrics/today');
          const metrics = await metricsRes.json();
          document.getElementById('plays_today').textContent = metrics.plays_today;
        }}

        refresh();
        setInterval(refresh, 5000);
      </script>
    </body>
    </html>
    """


# ----------------- CREATE EXTRA SCREENS -----------------
@app.post("/api/screens/create")
def create_screen(screen: ScreenCreate):
    db = next(get_db())
    new_screen = Screen(
        name=screen.name,
        company_id=screen.company_id,
        status="offline"
    )
    db.add(new_screen)
    db.commit()
    db.refresh(new_screen)
    return {
        "id": new_screen.id,
        "name": new_screen.name,
        "company_id": new_screen.company_id
    }

@app.post("/api/screens/create-bulk")
def create_bulk_screens(
    count: int = Form(...),
    company_id: int = Form(...)
):
    db = next(get_db())

    created = []
    for i in range(count):
        name = f"Screen {i + 1}"
        s = Screen(name=name, company_id=company_id, status="offline")
        db.add(s)
        db.flush()
        created.append({"id": s.id, "name": s.name})

    db.commit()

    return {
        "message": f"{count} screens created",
        "screens": created
    }



# ----------------- UPTIME METRICS API -----------------
@app.get("/api/metrics/uptime")
def uptime_metrics(window_minutes: int = 60):
    """
    Approximate uptime for each screen over the last N minutes,
    based on heartbeats.
    """
    db = next(get_db())
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=window_minutes)

    screens = db.query(Screen).all()
    results = []

    for s in screens:
        hb_first = (
            db.query(Heartbeat)
            .filter(
                Heartbeat.screen_id == s.id,
                Heartbeat.timestamp >= window_start,
                Heartbeat.timestamp <= now,
            )
            .order_by(Heartbeat.timestamp.asc())
            .first()
        )
        hb_last = (
            db.query(Heartbeat)
            .filter(
                Heartbeat.screen_id == s.id,
                Heartbeat.timestamp >= window_start,
                Heartbeat.timestamp <= now,
            )
            .order_by(Heartbeat.timestamp.desc())
            .first()
        )

        if not hb_first or not hb_last:
            uptime_percent = 0.0
        else:
            observed = (hb_last.timestamp - hb_first.timestamp).total_seconds()
            total = window_minutes * 60
            uptime_percent = max(0.0, min(100.0, (observed / total) * 100.0))

        results.append({
            "screen_id": s.id,
            "screen_name": s.name,
            "window_minutes": window_minutes,
            "uptime_percent": round(uptime_percent, 2),
        })

    return results


# ----------------- UPTIME DASHBOARD -----------------
@app.get("/uptime", response_class=HTMLResponse)
def uptime_dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Screen Uptime Dashboard</title>
      <meta charset="utf-8" />
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>
        body {{
          font-family: Arial, sans-serif;
          padding: 0;
          margin:0;
          background: #050814;
          color:#e5e5e5;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:800px;
        }}
        .card {{
          background: #111827;
          padding: 16px 20px;
          border-radius: 12px;
          box-shadow: 0 0 20px rgba(0,0,0,0.4);
          margin:16px 0;
        }}
        label {{ font-weight: bold; }}
        input {{
          padding: 4px 8px;
          border-radius:6px;
          border:1px solid #374151;
          background:#020617;
          color:#e5e5e5;
          margin-right:8px;
        }}
        button {{
          padding:6px 12px;
          border-radius:6px;
          border:none;
          background:linear-gradient(90deg,#00e5ff,#00bcd4);
          color:#000;
          font-weight:bold;
          cursor:pointer;
        }}
        button:hover {{ opacity:0.9; }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div style="height:8px;"></div>

      <div class="page">
        <div class="container">
          <div class="card">
            <h1>Screen Uptime Dashboard</h1>
            <p>
              <label>Window (minutes): </label>
              <input type="number" id="windowMinutes" value="60" min="5" max="1440" />
              <button onclick="loadData()">Refresh</button>
            </p>
            <canvas id="uptimeChart" height="120"></canvas>
          </div>
        </div>
      </div>

      <script>
        let chart;

        async function loadData() {{
          const win = document.getElementById('windowMinutes').value || 60;
          const res = await fetch('/api/metrics/uptime?window_minutes=' + win);
          const data = await res.json();

          const labels = data.map(d => d.screen_name + ' (#' + d.screen_id + ')');
          const values = data.map(d => d.uptime_percent);

          const ctx = document.getElementById('uptimeChart').getContext('2d');

          if (chart) chart.destroy();

          chart = new Chart(ctx, {{
            type: 'bar',
            data: {{
              labels,
              datasets: [{{
                label: 'Uptime % (last ' + win + ' minutes)',
                data: values
              }}]
            }},
            options: {{
              responsive: true,
              scales: {{
                y: {{ beginAtZero: true, max: 100 }}
              }}
            }}
          }});
        }}

        loadData();
        setInterval(loadData, 30000); // refresh every 30s
      </script>
    </body>
    </html>
    """


# ----------------- DEBUG LOG APIS & LOGS PAGE -----------------
@app.get("/api/debug/heartbeats")
def debug_heartbeats(limit: int = 50):
    db = next(get_db())
    rows = (
        db.query(Heartbeat)
        .order_by(Heartbeat.timestamp.desc())
        .limit(limit)
        .all()
    )
    out = []
    for hb in rows:
        out.append({
            "id": hb.id,
            "screen_id": hb.screen_id,
            "timestamp": hb.timestamp.isoformat() if hb.timestamp else None,
            "current_ad_id": hb.current_ad_id,
            "player_status": hb.player_status,
        })
    return out


@app.get("/api/debug/playback-events")
def debug_playback_events(limit: int = 50):
    db = next(get_db())
    rows = (
        db.query(PlaybackEvent)
        .order_by(PlaybackEvent.started_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for ev in rows:
        out.append({
            "id": ev.id,
            "screen_id": ev.screen_id,
            "ad_id": ev.ad_id,
            "started_at": ev.started_at.isoformat() if ev.started_at else None,
            "ended_at": ev.ended_at.isoformat() if ev.ended_at else None,
            "status": ev.status,
        })
    return out


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Logs Viewer</title>
      <meta charset="utf-8" />
      <style>
        body {{
          font-family: Arial, sans-serif;
          padding: 0;
          margin:0;
          background: #050814;
          color:#e5e5e5;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:1000px;
        }}
        .card {{
          background: #111827;
          padding: 16px 20px;
          border-radius: 12px;
          box-shadow: 0 0 20px rgba(0,0,0,0.4);
          margin:16px 0;
        }}
        table {{
          border-collapse: collapse;
          width: 100%;
          font-size: 12px;
        }}
        th, td {{
          border: 1px solid #1f2933;
          padding: 4px 6px;
        }}
        th {{
          background: #111827;
          color:#e5e5e5;
        }}
        tr:nth-child(even) td {{
          background:#020617;
        }}
        tr:nth-child(odd) td {{
          background:#020617;
        }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div style="height:8px;"></div>

      <div class="page">
        <div class="container">
          <div class="card">
            <h1>Logs Viewer</h1>

            <h2>Recent Heartbeats</h2>
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Screen</th><th>Timestamp</th><th>Current Ad</th><th>Status</th>
                </tr>
              </thead>
              <tbody id="hbTable"></tbody>
            </table>
          </div>

          <div class="card">
            <h2>Recent Playback Events</h2>
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Screen</th><th>Ad</th><th>Started</th><th>Ended</th><th>Status</th>
                </tr>
              </thead>
              <tbody id="pbTable"></tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        async function loadHeartbeats() {{
          const res = await fetch('/api/debug/heartbeats?limit=50');
          const data = await res.json();
          const tbody = document.getElementById('hbTable');
          tbody.innerHTML = '';
          data.forEach(hb => {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td>${{hb.id}}</td>
              <td>${{hb.screen_id}}</td>
              <td>${{hb.timestamp || ''}}</td>
              <td>${{hb.current_ad_id || ''}}</td>
              <td>${{hb.player_status || ''}}</td>
            `;
            tbody.appendChild(tr);
          }});
        }}

        async function loadPlayback() {{
          const res = await fetch('/api/debug/playback-events?limit=50');
          const data = await res.json();
          const tbody = document.getElementById('pbTable');
          tbody.innerHTML = '';
          data.forEach(ev => {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td>${{ev.id}}</td>
              <td>${{ev.screen_id}}</td>
              <td>${{ev.ad_id}}</td>
              <td>${{ev.started_at || ''}}</td>
              <td>${{ev.ended_at || ''}}</td>
              <td>${{ev.status || ''}}</td>
            `;
            tbody.appendChild(tr);
          }});
        }}

        async function refreshAll() {{
          await loadHeartbeats();
          await loadPlayback();
        }}

        refreshAll();
        setInterval(refreshAll, 10000); // refresh every 10 seconds
      </script>
    </body>
    </html>
    """


# ----------------- ADS MANAGEMENT PAGE -----------------
@app.get("/ads/manage", response_class=HTMLResponse)
def ads_manage_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Manage Ads</title>
      <meta charset="utf-8" />
      <style>
        body {{
          font-family: Arial, sans-serif;
          padding: 0;
          margin:0;
          background: #050814;
          color:#e5e5e5;
        }}
        .page {{
          display:flex;
          justify-content:center;
          padding:24px 16px;
        }}
        .container {{
          width:100%;
          max-width:800px;
        }}
        .card {{
          background: #111827;
          padding: 16px 20px;
          border-radius: 12px;
          box-shadow: 0 0 20px rgba(0,0,0,0.4);
          margin:16px 0;
        }}
        .label {{
          font-weight: bold;
          display: block;
          margin-bottom: 4px;
        }}
        input, button {{
          padding: 6px 10px;
          margin-bottom: 10px;
          border-radius:6px;
          border:1px solid #374151;
          background:#020617;
          color:#e5e5e5;
        }}
        input[type="file"] {{
          border:none;
        }}
        button {{
          background:linear-gradient(90deg,#00e5ff,#00bcd4);
          color:#000;
          font-weight:bold;
          cursor:pointer;
        }}
        button:hover {{
          opacity:0.9;
        }}
        table {{
          border-collapse: collapse;
          width: 100%;
          margin-top: 10px;
          font-size: 13px;
        }}
        th, td {{
          border: 1px solid #1f2933;
          padding: 6px 8px;
        }}
        th {{
          background: #111827;
          color:#e5e5e5;
        }}
        tr:nth-child(even) td {{
          background:#020617;
        }}
        tr:nth-child(odd) td {{
          background:#020617;
        }}
      </style>
    </head>
    <body>
      {NAV_HTML}
      <div style="height:8px;"></div>

      <div class="page">
        <div class="container">
          <div class="card">
            <h1>Ad Management</h1>
            <h2>Upload New Ad</h2>
            <form id="uploadForm">
              <label class="label">Ad Name</label>
              <input type="text" name="name" required /><br/>
              <label class="label">Owner Company ID</label>
              <input type="number" name="owner_company_id" value="1" /><br/>
              <label class="label">Video File</label>
              <input type="file" name="file" accept="video/*" required /><br/>
              <button type="submit">Upload</button>
            </form>
            <div id="uploadStatus"></div>
          </div>

          <div class="card">
            <h2>Current Ads</h2>
            <table>
              <thead>
                <tr><th>ID</th><th>Name</th><th>Owner</th><th>File URL</th></tr>
              </thead>
              <tbody id="adsTable"></tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        async function loadAds() {{
          const res = await fetch('/api/ads');
          const ads = await res.json();
          const tbody = document.getElementById('adsTable');
          tbody.innerHTML = '';
          ads.forEach(ad => {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td>${{ad.id}}</td>
              <td>${{ad.name}}</td>
              <td>${{ad.owner_company_id}}</td>
              <td><a href="${{ad.file_url}}" target="_blank">${{ad.file_url}}</a></td>
            `;
            tbody.appendChild(tr);
          }});
        }}

        const form = document.getElementById('uploadForm');
        form.addEventListener('submit', async (e) => {{
          e.preventDefault();
          const formData = new FormData(form);
          const res = await fetch('/api/ads/upload', {{
            method: 'POST',
            body: formData
          }});
          const data = await res.json();
          document.getElementById('uploadStatus').textContent = data.message || 'Uploaded';
          loadAds();
        }});

        loadAds();
      </script>
    </body>
    </html>
    """