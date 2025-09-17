# 🚨 SINGLE PROMPT TO BUILD A ONE-FILE AI-PROCTORED TEST APP

**Role:** You are a senior ML engineer.
**Task:** Generate **one** self-contained Python source file that implements an **AI-proctored exam system** using **YOLO (Ultralytics)** for object detection, **MediaPipe** for face landmarks/gaze, and **Gemini** for event fusion and risk scoring.
**Non-negotiable:** **All code must be in a single `.py` file**. Do **not** create additional files or folders. Embed any HTML/CSS/JS/templates as Python strings if needed. Integrate a **web UI** (Streamlit preferred; Gradio acceptable) in the same file for live interaction, settings, logs, previews, and exports.

## Output requirements

* Output **exactly one** Python file named **`proctor_ai.py`** (no other files).
* The file must be **runnable as-is** after `pip install` steps.
* Include a **top-of-file README comment** with:

  * Python version recommendation (3.10–3.12), install steps, and how to run.
  * Required environment variable: `GEMINI_API_KEY`.
  * Example commands:

    * `pip install -U ultralytics opencv-python mediapipe google-genai streamlit pydantic uvicorn`
      (and fallback note: if `google-genai` unavailable, use `google-generativeai`.)
    * `streamlit run proctor_ai.py`
* The app must **auto-download** a default YOLO model (prefer `yolo11n.pt`; fallback to `yolov8n.pt` if not found).
* Provide **Windows-friendly** notes (e.g., if webcam access fails, suggest checking privacy settings and DirectShow).

## High-level features (MVP → Advanced)

1. **Live capture & processing**

   * Access webcam (OpenCV) in a background thread.
   * Real-time YOLO detection for: `person, cell phone/phone, laptop, monitor/tv, earbud/earphone/headset, book/paper`.
   * MediaPipe Face Mesh/Face Landmarker for **gaze** (yaw/pitch) and simple **mouth movement** heuristic (talking).
   * Optional audio VAD stub (can be toggled off if mic unavailable).
   * Save **evidence snapshots** (annotated frame) to a temp folder; but since single-file is required, ensure folder creation happens at runtime only (e.g., `./evidence/`).

2. **Agentic pipeline (in code)**

   * Implement small **agents** as classes/functions:

     * `ObjectAgent` (YOLO detect/track)
     * `FaceAgent` (MediaPipe landmarks → yaw/pitch & mouth-open ratio)
     * `AudioAgent` (optional VAD stub returning empty by default)
     * `ScreenAgent` (stubbed; future extension)
     * `AggregatorAgent` (rules + Gemini reasoning)
   * Use a simple **event bus** (Python list with thread-safe queue or lock) to collect events in **5-second windows**.

3. **Event schema (use Pydantic)**

   * Define `Event` model:

     ```json
     {
       "ts": "ISO-8601",
       "source": "ObjectAgent|FaceAgent|AudioAgent|ScreenAgent",
       "type": "phone|earbuds|extra_person|book|monitor|laptop|gaze_away|mouth_moving|speaking|face_not_visible",
       "duration_ms": 0,
       "bbox": [x, y, w, h],
       "confidence": 0.0,
       "frame_path": "evidence/frame_000123.jpg",
       "notes": "string"
     }
     ```
   * Provide a helper `push_event(**kwargs)` that timestamps and appends validated events.

4. **Scoring rubric (transparent & auditable)**

   * Weights (sum 100):

     * Device presence **30** (phones/earbuds/books/extra-person; weight by duration & confidence)
     * Gaze/pose **25** (off-screen gaze >3s, yaw>|35°| or pitch>|25°|)
     * Speaking **20** (VAD hits during silent sections)
     * Visibility **15** (face missing/covered)
     * Screen behavior **10** (tab switching; keep as stub)
   * Normalize to **0–100** and map to:

     * **Low**: 0–39
     * **Medium**: 40–69
     * **High**: 70–100
   * Rules: require either ≥2 independent signals **or** ≥N seconds of a single strong signal before escalating to Medium/High. N defaults to 6s (configurable in UI).

5. **Gemini integration (structured output)**

   * Create `GeminiClient` wrapper:

     * Prefer `google-genai` (`from google import genai`), fallback to `google-generativeai`.
     * Accepts `events_window` + `rubric` + `policy`.
     * Requests **JSON** response; on failure, returns a safe default JSON.
   * Prompt includes:

     * The rubric weights and thresholds.
     * Policy: “Do not accuse without ≥2 independent signals or ≥N seconds sustained duration; cite evidence frame paths.”
     * Events list (5s window).
   * Output JSON schema (Pydantic):

     ```json
     {
       "risk_score": 0,
       "risk_band": "low|medium|high",
       "incidents": [
         {
           "label": "phone_detected|gaze_away|speaking|…",
           "summary": "short explanation",
           "evidence": ["evidence/frame_000123.jpg"]
         }
       ],
       "window_start": "ISO-8601",
       "window_end": "ISO-8601"
     }
     ```

6. **Web UI (single-file)**

   * Use **Streamlit** in the same file to render:

     * **Header** with privacy notice and consent checkbox (must be checked to start).
     * **Sidebar Controls**:

       * Start/Stop capture
       * Model select: `yolo11n.pt` or `yolov8n.pt`
       * Confidence slider (0.2–0.6), NMS threshold, frame size
       * Gaze thresholds (yaw/pitch), talking sensitivity
       * Window length (default 5s), sustained-duration N for escalation
       * Toggle audio & screen agents (stubs)
       * Log retention limit and evidence snapshot toggle
       * Gemini model (`gemini-2.5-flash` default), API key status
     * **Main Panels**:

       * Live annotated video (latest frame)
       * Current **Risk Score** + **Band** (big badges)
       * **Incidents table** (label, summary, confidence, duration, links to evidence)
       * **Events raw JSON** (collapsible) for audit
       * Buttons: **Export CSV** (events + reports), **Clear Log**
   * Implement a safe main loop:

     * A background thread captures & processes frames (YOLO + MediaPipe) and pushes `Event`s.
     * Every 5s, aggregate window → call `AggregatorAgent` (rules + Gemini) → update UI.
     * Ensure proper thread cleanup on Stop/exit.

7. **Data handling & privacy**

   * Show a **consent banner** (must be checked to enable processing).
   * Avoid uploading full video. If calling Gemini with images, **send only cropped evidence frames** when possible. Add a UI toggle “Send frames to LLM” (off by default).
   * Provide configurable **retention**: max evidence images to keep; older files auto-deleted.
   * Display a **Privacy & Use** section in the UI (plain text) explaining data handling.

8. **Implementation details**

   * **YOLO**: `ultralytics.YOLO("yolo11n.pt")` with fallback; detection loop at \~640px; track optional.
   * **Face landmarks**: `mediapipe.solutions.face_mesh.FaceMesh(refine_landmarks=True, max_num_faces=2)`
   * **Head pose heuristic**: compute yaw/pitch from eye/nose landmarks; if available, implement optional PnP with a canonical head model for better accuracy.
   * **Talking heuristic**: simple mouth aspect ratio from lip landmarks; mark as `mouth_moving` when above threshold for ≥1s.
   * **Threading**: use `threading.Thread` for capture; `queue.Queue` or `collections.deque` for frames/events; locks as needed.
   * **Annotations**: draw boxes/labels on frames; overlay current risk band.
   * **Logging**: in-memory list for events and reports; CSV export using `pandas` (but do not create separate modules/files).
   * **Graceful errors**: if GPU not available, run CPU; if model download fails, show UI alert; if webcam busy, show actionable tip.
   * **Windows paths**: ensure `os.path.join` usage; create `./evidence` at runtime if missing.

9. **Config & constants**

   * Put defaults in a single `Config` dataclass (thresholds, weights, model names).
   * Allow overrides via Streamlit sidebar.

10. **Acceptance tests (lightweight, in-code)**

* Include a small `if __name__ == "__main__":` smoke test for:

  * Model load
  * One dummy frame pass through detectors (synthetic blank if webcam not available)
  * JSON validation with Pydantic
* If webcam unavailable, the app still launches UI and allows playing a sample video file (if user selects one) to simulate the pipeline.

11. **What to avoid**

* No multi-file packages, no external templates, no `.env` files.
* No long blocking loops in the main Streamlit thread; keep processing in background threads and update UI elements iteratively.
* No hardcoded secrets. Read `GEMINI_API_KEY` from env; provide UI text field to set it for the session (stored only in memory).

## Example UI copy (to include)

* Consent text: “By enabling proctoring, you consent to live video analysis. Evidence snapshots are stored locally and can be deleted anytime from this UI. Only cropped evidence frames are optionally sent to Gemini if you enable that toggle.”
* Privacy note: “This demo is for educational purposes. Review your local regulations before using it in real exams.”

## Minimal pseudo-structure (guide for your code generator)

```
# proctor_ai.py
# 1) README comment + installs + how to run
# 2) Imports, constants, Config dataclass, Pydantic models (Event, GeminiReport)
# 3) Helpers: ensure_dirs(), save_evidence(frame), draw_annotations(), head_pose(), mouth_ratio()
# 4) Agents: ObjectAgent, FaceAgent, AudioAgent (stub), ScreenAgent (stub)
# 5) EventBus / WindowBuffer
# 6) Scoring rules + AggregatorAgent (rules + GeminiClient)
# 7) GeminiClient (google-genai preferred; generativeai fallback; structured JSON)
# 8) CaptureThread (OpenCV) → detection pipeline → push events
# 9) Streamlit UI: sidebar controls, start/stop, live frame, score, incidents, JSON, export
# 10) Main: init, thread lifecycle, periodic window aggregation, safe shutdown

