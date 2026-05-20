from flask import Flask, send_from_directory, render_template, abort, request, jsonify, url_for
import os
import subprocess
import sys
import urllib.parse
import threading
import uuid
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STATIC_DIR = os.path.join(ROOT, 'server_content')

# Use environment variables or defaults for download directories
AUDIO_DIR = os.environ.get('AUDIO_DOWNLOAD_DIR', r'D:\Server_Output\Audio')
VIDEO_DIR = os.environ.get('VIDEO_DOWNLOAD_DIR', r'D:\Server_Output\Video')

# Database configuration from environment or defaults
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_USER = os.environ.get('DB_USER', 'webuser')
DB_PASS = os.environ.get('DB_PASS', 'password')
DB_NAME = os.environ.get('DB_NAME', 'home_server')

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=STATIC_DIR)
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)

# In-memory job tracker
download_jobs = {}
jobs_lock = threading.Lock()


def is_valid_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ('http', 'https') and parsed.netloc


def get_latest_file_in_dir(directory):
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            files.append(os.path.join(root, filename))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def format_file_size_str(bytes_val):
    """Format bytes to human-readable string."""
    if bytes_val == 0:
        return '0 B'
    k = 1024
    sizes = ['B', 'KB', 'MB', 'GB']
    i = min(3, len(sizes) - 1)
    for idx in range(len(sizes)):
        if bytes_val < k ** (idx + 1):
            i = idx
            break
    return f'{bytes_val / (k ** i):.2f} {sizes[i]}'


def format_speed_str(bytes_per_second):
    """Format a speed value into human-readable string per second."""
    if bytes_per_second is None or bytes_per_second == 0:
        return '--'
    speed_str = format_file_size_str(bytes_per_second)
    return f'{speed_str}/s'


def format_eta_str(seconds):
    """Convert seconds to HH:MM:SS string."""
    if seconds is None or seconds < 0:
        return '--'
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def run_download_in_background(job_id, url, mode):
    """Run yt-dlp using its Python API and update progress via hooks."""
    try:
        # Import yt_dlp here to fail gracefully if it's not available
        import yt_dlp

        with jobs_lock:
            download_jobs[job_id]['status'] = 'downloading'

        download_dir = AUDIO_DIR if mode == 'audio' else VIDEO_DIR
        output_template = os.path.join(download_dir, '%(title)s [%(id)s].%(ext)s')

        ydl_opts = {
            'outtmpl': output_template,
            'noplaylist': True,
            'nooverwrites': True,
            'quiet': True,
        }

        if mode == 'audio':
            # extract audio to mp3
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                }],
            })
        else:
            # download best video+audio and merge to mp4
            ydl_opts.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
            })

        # progress hook updates download_jobs
        def progress_hook(d):
            try:
                with jobs_lock:
                    if d.get('status') == 'downloading':
                        downloaded = d.get('downloaded_bytes')
                        total = d.get('total_bytes') or d.get('total_bytes_estimate')
                        speed = d.get('speed')
                        eta = d.get('eta')
                        if downloaded is None:
                            return
                        if total:
                            pct = (downloaded / total) * 100 if total > 0 else 0
                            download_jobs[job_id]['progress_percent'] = min(100.0, pct)
                            download_jobs[job_id]['total_size_str'] = format_file_size_str(total)
                        else:
                            download_jobs[job_id]['progress_percent'] = 0
                            download_jobs[job_id]['total_size_str'] = format_file_size_str(downloaded)
                        if speed is not None:
                            download_jobs[job_id]['speed_str'] = format_speed_str(speed)
                        if eta is not None:
                            download_jobs[job_id]['eta_str'] = format_eta_str(eta)
                    elif d.get('status') == 'finished':
                        filename = d.get('filename')
                        if filename:
                            try:
                                file_size = os.path.getsize(filename)
                            except OSError:
                                file_size = 0
                            download_jobs[job_id]['status'] = 'completed'
                            download_jobs[job_id]['download_path'] = filename
                            download_jobs[job_id]['file_name'] = os.path.basename(filename)
                            download_jobs[job_id]['file_size'] = file_size
                            download_jobs[job_id]['progress_percent'] = 100
                            download_jobs[job_id]['total_size_str'] = format_file_size_str(file_size)
                            download_jobs[job_id]['speed_str'] = '--'
                            download_jobs[job_id]['eta_str'] = '00:00:00'
            except Exception as e:
                # Do not let hook exceptions kill the download; record the error for debugging
                with jobs_lock:
                    download_jobs[job_id].setdefault('hook_errors', []).append(str(e))

        ydl_opts['progress_hooks'] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = str(e)
            return

        # If hook didn't set completed (some extractors may not call finished), set it now
        latest_file = get_latest_file_in_dir(download_dir)
        if latest_file:
            file_size = os.path.getsize(latest_file)
            file_name = os.path.basename(latest_file)
            with jobs_lock:
                download_jobs[job_id]['status'] = 'completed'
                download_jobs[job_id]['download_path'] = latest_file
                download_jobs[job_id]['file_name'] = file_name
                download_jobs[job_id]['file_size'] = file_size
                download_jobs[job_id]['progress_percent'] = 100
                download_jobs[job_id]['total_size_str'] = format_file_size_str(file_size)
        else:
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = 'Download finished but output file not found'

    except ImportError:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = 'yt_dlp Python package not installed'
    except Exception as e:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = str(e)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download/<job_id>', methods=['GET'])
def download_file(job_id):
    with jobs_lock:
        job = download_jobs.get(job_id)
        if not job:
            abort(404)
        if job['status'] != 'completed' or not job.get('download_path'):
            abort(404)
        file_path = os.path.abspath(job['download_path'])

    allowed_dirs = [os.path.abspath(AUDIO_DIR), os.path.abspath(VIDEO_DIR)]
    if not any(os.path.commonpath([file_path, allowed_dir]) == allowed_dir for allowed_dir in allowed_dirs):
        abort(403)

    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    return send_from_directory(directory, filename, as_attachment=True)


def get_db_connection():
    import pymysql
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.Cursor,
    )


@app.route('/db_browser', methods=['GET', 'POST'])
def db_browser():
    error = None
    success = None
    selected_table = request.args.get('table')
    table_headers = []
    table_rows = []
    tables = []

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SHOW TABLES')
        tables = [row[0] for row in cursor.fetchall()]

        if request.method == 'POST' and request.form.get('action') == 'guestbook_add':
            name = (request.form.get('name') or '').strip()
            if not name:
                error = 'Name cannot be empty.'
            else:
                try:
                    cursor.execute('INSERT INTO guestbook (name) VALUES (%s)', (name,))
                    conn.commit()
                    success = 'Thank you — your name was added.'
                except Exception as exc:
                    error = 'Insert failed: ' + str(exc)

        if selected_table:
            if selected_table not in tables:
                error = 'Requested table is not available.'
            else:
                cursor.execute(f'SELECT * FROM `{selected_table}`')
                table_rows = cursor.fetchall()
                table_headers = list(cursor.column_names) if cursor.description else []

        cursor.close()
        conn.close()
    except Exception as exc:
        error = str(exc)

    return render_template(
        'db_browser.html',
        db_name=DB_NAME,
        tables=tables,
        selected_table=selected_table,
        table_headers=table_headers,
        table_rows=table_rows,
        error=error,
        success=success,
    )


@app.route('/<path:filename>')
def serve_file(filename):
    # Prefer rendering HTML templates from the server_content folder
    if filename.endswith('.html') and os.path.exists(os.path.join(app.template_folder, filename)):
        return render_template(filename)
    # Fall back to static files (css, js, images, etc.)
    if os.path.exists(os.path.join(app.static_folder, filename)):
        return send_from_directory(app.static_folder, filename)
    abort(404)


@app.route('/api/download', methods=['POST'])
def download_api():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    url = (data.get('url') or '').strip()
    mode = (data.get('mode') or 'video').strip().lower()

    if not url:
        return jsonify(error='Missing YouTube URL'), 400
    if not is_valid_url(url):
        return jsonify(error='Invalid URL'), 400
    if mode not in ('audio', 'video'):
        return jsonify(error='Invalid mode'), 400

    # Create a unique job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job in tracker
    with jobs_lock:
        download_jobs[job_id] = {
            'status': 'queued',
            'url': url,
            'mode': mode,
            'job_id': job_id,
            'started_at': time.time(),
            'error': None,
            'download_path': None,
            'file_name': None,
            'file_size': 0,
            'progress_percent': 0,
            'total_size_str': '--',
            'speed_str': '--',
            'eta_str': '--',
        }
    
    # Start download in background thread
    thread = threading.Thread(target=run_download_in_background, args=(job_id, url, mode))
    thread.daemon = True
    thread.start()
    
    return jsonify(
        job_id=job_id,
        status='queued',
        message='Download started',
    ), 202


@app.route('/api/download-status/<job_id>', methods=['GET'])
def download_status(job_id):
    """Check the status of a download job."""
    with jobs_lock:
        if job_id not in download_jobs:
            return jsonify(error='Job not found'), 404
        
        job = download_jobs[job_id].copy()

    if job['status'] == 'completed' and job.get('file_name'):
        job['download_url'] = url_for('download_file', job_id=job_id)
    else:
        job['download_url'] = None
    
    # Clean up completed jobs after returning (optional: keep for a period)
    if job['status'] in ('completed', 'failed'):
        # Could add cleanup logic here
        pass
    
    return jsonify(job)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
