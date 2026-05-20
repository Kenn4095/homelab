# Flask server for existing `server_content`

This minimal Flask app serves the existing files in the `server_content` directory as static files and Jinja templates.

Quick start (Windows):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000/

Notes:
- The app uses `server_content` as both the static folder and template folder, so existing HTML/CSS can be served without modification.
- `/api/download` receives POST requests and runs `yt-dlp` to save downloads into `server_content/downloads`.
- Open `youtube_downloader.html`, enter a YouTube URL, and choose Audio or Video to download.
