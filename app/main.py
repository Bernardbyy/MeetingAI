"""MeetingAI — FastAPI backend.

Phase 3: capture and transcription run together; Stop only waits for the tail.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import audio, export, session, settings, storage, summarize, transcribe

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="MeetingAI")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def always_revalidate(request, call_next):
    """Make the browser check for a newer copy on every load (a cheap 304 when
    nothing changed). Without it, after an update it can pair a new index.html
    with a cached old app.js, and new buttons silently do nothing."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response
settings.apply()

# ponytail: single user, one meeting at a time, so one module-level slot is
# the whole session state.
CURRENT: session.Session | None = None

IDLE = {"stage": "idle", "meeting_id": None, "elapsed_sec": 0, "stage_sec": 0,
        "closed": 0, "transcribed": 0, "batch": 0, "batches": 0, "devices": {},
        "models": {}, "errors": []}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    if CURRENT:
        return CURRENT.status()
    # Idle still names the devices and models, so the app can say what it
    # would record with before anything has been recorded.
    idle = dict(IDLE)
    cfg = settings.load()
    idle["models"] = {"whisper": cfg["whisper_model"], "llm": cfg["llm_model"]}
    try:
        found = audio.list_devices()
        by_index = {d["index"]: d["name"] for d in found["mics"] + found["loopbacks"]}
        idle["devices"] = {
            "mic": by_index.get(cfg["mic_index"] if cfg["mic_index"] is not None
                                else found["default_mic"], ""),
            "loopback": by_index.get(cfg["loopback_index"] if cfg["loopback_index"] is not None
                                     else found["default_loopback"], ""),
        }
    except Exception as e:
        idle["errors"] = [f"audio devices unavailable: {e}"]
    return idle


@app.post("/api/start")
def start():
    global CURRENT
    if CURRENT is not None:
        raise HTTPException(409, f"already {CURRENT.stage}")

    cfg = settings.load()
    meeting_id = storage.new_meeting()
    s = session.Session(
        meeting_id,
        model_name=cfg["whisper_model"],
        llm_model=cfg["llm_model"],
        mic_index=cfg["mic_index"],
        loopback_index=cfg["loopback_index"],
    )
    try:
        found = s.start()
    except Exception as e:
        storage.delete_meeting(meeting_id)  # nothing captured, leave no folder
        raise HTTPException(500, f"could not start capture: {e}")

    storage.update_meta(meeting_id, devices=found, whisper_model=s.model_name,
                        llm_model=s.llm_model)
    CURRENT = s
    return s.status()


@app.post("/api/stop")
def stop():
    global CURRENT
    if CURRENT is None:
        raise HTTPException(409, "not recording")

    s = CURRENT
    try:
        result = s.stop()
    finally:
        CURRENT = None

    storage.update_meta(
        s.meeting_id,
        ended_at=storage._now(),
        duration_sec=result["duration_sec"],
        capture_errors=result["errors"],
    )
    result["transcript_lines"] = len(result.pop("transcript").splitlines())
    return result


@app.get("/api/settings")
def get_settings():
    """Current settings plus everything the panel needs to offer choices."""
    try:
        devices = audio.list_devices()
    except Exception as e:
        devices = {"mics": [], "loopbacks": [], "error": str(e)}
    return {
        "settings": settings.load(),
        "whisper_models": settings.WHISPER_MODELS,
        "whisper_downloaded": transcribe.downloaded_models(settings.WHISPER_MODELS),
        "llm_models": summarize.list_models(),
        "devices": devices,
    }


@app.post("/api/settings")
def post_settings(patch: dict):
    if CURRENT is not None:
        raise HTTPException(409, "stop the recording before changing settings")
    try:
        return settings.save(patch)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/meetings")
def meetings():
    return storage.list_meetings()


@app.get("/api/meetings/{meeting_id}")
def meeting(meeting_id: str):
    try:
        return storage.read_meeting(meeting_id)
    except FileNotFoundError:
        raise HTTPException(404, "no such meeting")
    except ValueError:
        raise HTTPException(400, "bad meeting id")


@app.put("/api/meetings/{meeting_id}/minutes")
def save_minutes(meeting_id: str, body: dict):
    # While a meeting is still being processed, Stop would overwrite the edit.
    if CURRENT is not None and CURRENT.meeting_id == meeting_id:
        raise HTTPException(409, "that meeting is still being processed")
    text = body.get("minutes")
    if not isinstance(text, str):
        raise HTTPException(400, "minutes must be text")
    try:
        storage.write_minutes(meeting_id, text)
    except FileNotFoundError:
        raise HTTPException(404, "no such meeting")
    except ValueError:
        raise HTTPException(400, "bad meeting id")
    return {"saved": meeting_id}


@app.get("/api/meetings/{meeting_id}/minutes.docx")
def export_minutes(meeting_id: str):
    try:
        m = storage.read_meeting(meeting_id)
    except FileNotFoundError:
        raise HTTPException(404, "no such meeting")
    except ValueError:
        raise HTTPException(400, "bad meeting id")
    if not m["minutes"].strip():
        raise HTTPException(404, "this meeting has no minutes")

    # meeting_id is 2026-09-30_1138[_slug]: show it as a date and time.
    day, hhmm = meeting_id.split("_")[:2]
    when = f"{day} {hhmm[:2]}:{hhmm[2:]}"
    secs = m.get("duration_sec")
    subtitle = " · ".join(filter(None, [
        when,
        f"{round(secs / 60)} min" if secs else None,
        "reviewed" if m.get("minutes_edited_at") else "draft, not yet reviewed",
    ]))
    data = export.minutes_docx(m["minutes"], "Meeting minutes", subtitle)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f'attachment; filename="Meeting minutes {day} {hhmm}.docx"'},
    )


@app.delete("/api/meetings/{meeting_id}")
def remove(meeting_id: str):
    if CURRENT is not None and CURRENT.meeting_id == meeting_id:
        raise HTTPException(409, "that meeting is still recording")
    try:
        storage.delete_meeting(meeting_id)
    except FileNotFoundError:
        raise HTTPException(404, "no such meeting")
    except ValueError:
        raise HTTPException(400, "bad meeting id")
    return {"deleted": meeting_id}
