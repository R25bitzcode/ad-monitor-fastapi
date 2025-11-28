from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

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

    # Create two ads using your new videos (Vid1.mp4, Vid2.mp4 in /media)
    my_ad = Ad(
        name="Vid 1",
        owner_company_id=central_company.id,
        file_url="/media/Vid1.mp4",
        duration_sec=10,  # approximate; for info/UX only
    )
    other_ad = Ad(
        name="Vid 2",
        owner_company_id=central_company.id + 999,  # simulate "other company"
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


# ----------------- REAL PLAYER PAGE -----------------
@app.get("/player/screen/{screen_id}", response_class=HTMLResponse)
def player_page(screen_id: int):
    # Simple browser player that fetches playlist and loops ads
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
        video {{
          width: 100vw;
          height: 100vh;
          object-fit: cover;
          background: black;
        }}
      </style>
    </head>
    <body>
      <video id="video" autoplay playsinline></video>

      <script>
        const SCREEN_ID = {screen_id};
        const BASE_URL = window.location.origin;
        const video = document.getElementById("video");
        let playlist = [];
        let index = 0;

        async function fetchPlaylist() {{
          const res = await fetch(BASE_URL + "/api/screens/" + SCREEN_ID + "/playlist");
          const data = await res.json();
          playlist = data.playlist || [];
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
          }});
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
          }});
        }}

        function iso(d) {{ return d.toISOString(); }}

        async function playLoop() {{
          if (!playlist.length) {{
            await fetchPlaylist();
            if (!playlist.length) {{
              console.log("No ads found");
              setTimeout(playLoop, 5000);
              return;
            }}
          }}

          const ad = playlist[index];
          index = (index + 1) % playlist.length;

          video.src = ad.file_url;

          const start = new Date();
          sendHeartbeat(ad.id);

          video.onended = () => {{
            const end = new Date();
            sendPlayback(ad.id, iso(start), iso(end));
            playLoop();
          }};

          video.onerror = () => {{
            console.log("Error loading video", ad.file_url);
            playLoop();
          }};

          video.play().catch(err => {{
            console.error("Video play error", err);
          }});
        }}

        // Allow user interaction to start/unmute audio (mobile/desktop)
        video.addEventListener('click', () => {{
          video.muted = false;
          video.play().catch(err => {{
            console.error("Play with sound failed:", err);
          }});
        }});

        fetchPlaylist().then(playLoop);
      </script>
    </body>
    </html>
    """


# ----------------- SIMPLE DEMO DASHBOARD -----------------
@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Ad Monitoring Demo</title>
      <meta charset="utf-8" />
      <style>
        body { font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }
        .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); max-width: 420px; }
        .label { font-weight: bold; }
        .ok { color: green; font-weight: bold; }
        .bad { color: red; font-weight: bold; }
      </style>
    </head>
    <body>
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

      <script>
        async function refresh() {
          const currentRes = await fetch('/api/companies/1/screens/1/current-ad');
          const current = await currentRes.json();

          const onlineEl = document.getElementById('online');
          onlineEl.textContent = current.online ? 'Yes' : 'No';
          onlineEl.className = current.online ? 'ok' : 'bad';

          if (current.current_ad) {
            document.getElementById('current_ad').textContent = current.current_ad.name;
            const isYoursEl = document.getElementById('is_yours');
            isYoursEl.textContent = current.current_ad.is_yours ? 'Yes' : 'No';
            isYoursEl.className = current.current_ad.is_yours ? 'ok' : 'bad';
          } else {
            document.getElementById('current_ad').textContent = 'None';
            document.getElementById('is_yours').textContent = '-';
          }

          document.getElementById('heartbeat').textContent = current.last_heartbeat_at || 'N/A';

          const metricsRes = await fetch('/api/ads/1/metrics/today');
          const metrics = await metricsRes.json();
          document.getElementById('plays_today').textContent = metrics.plays_today;
        }

        refresh();
        setInterval(refresh, 5000);
      </script>
    </body>
    </html>
    """


# ----------------- CREATE EXTRA SCREENS -----------------
class ScreenCreate(BaseModel):
    name: str
    company_id: int = 1  # default central company


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
