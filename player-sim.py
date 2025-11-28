import time
import requests
from datetime import datetime, timedelta

BASE_URL = "http://127.0.0.1:8000"
SCREEN_ID = 1  # from /api/setup-sample
COMPANY_ID = 1 # central company

def iso(dt: datetime) -> str:
    # ISO-8601 with Z
    return dt.replace(microsecond=0).isoformat() + "Z"

def main():
    # 1) Fetch playlist for this screen
    print("Fetching playlist...")
    r = requests.get(f"{BASE_URL}/api/screens/{SCREEN_ID}/playlist")
    r.raise_for_status()
    data = r.json()
    playlist = data["playlist"]

    if not playlist:
        print("No ads in playlist, exiting.")
        return

    print("Playlist loaded:")
    for ad in playlist:
        print(f"- Ad {ad['id']}: {ad['name']} (owner_company_id={ad['owner_company_id']})")

    print("\nStarting playback loop. Ctrl+C to stop.\n")

    # 2) Loop through ads forever (demo)
    while True:
        for ad in playlist:
            ad_id = ad["id"]
            ad_name = ad["name"]
            duration = ad.get("duration_sec") or 5

            # a) Send heartbeat: "I am playing this ad now"
            hb_payload = {
                "screen_id": SCREEN_ID,
                "current_ad_id": ad_id,
                "player_status": "ok",
            }
            try:
                hb_res = requests.post(f"{BASE_URL}/api/events/heartbeat", json=hb_payload)
                hb_res.raise_for_status()
            except Exception as e:
                print(f"[ERROR] Heartbeat failed for ad {ad_id}: {e}")

            # b) Simulate playback
            start = datetime.utcnow()
            end = start + timedelta(seconds=duration)

            pb_payload = {
                "screen_id": SCREEN_ID,
                "ad_id": ad_id,
                "started_at": iso(start),
                "ended_at": iso(end),
                "status": "success",
            }
            try:
                pb_res = requests.post(f"{BASE_URL}/api/events/playback", json=pb_payload)
                pb_res.raise_for_status()
            except Exception as e:
                print(f"[ERROR] Playback event failed for ad {ad_id}: {e}")

            # c) Print local info for you
            print(f"[PLAYER] Screen {SCREEN_ID} played ad {ad_id} ({ad_name}) for {duration}s")

            # d) For fun: ask central what it thinks is playing now
            try:
                curr = requests.get(
                    f"{BASE_URL}/api/companies/{COMPANY_ID}/screens/{SCREEN_ID}/current-ad"
                ).json()
                print(
                    f"  -> Central says: online={curr['online']}, "
                    f"ad={curr['current_ad'] and curr['current_ad']['name']}, "
                    f"is_yours={curr['current_ad'] and curr['current_ad']['is_yours']}"
                )
            except Exception as e:
                print(f"[WARN] Could not fetch current-ad from central: {e}")

            # e) Sleep for the duration (simulate actual playback)
            time.sleep(duration)


if __name__ == "__main__":
    main()
