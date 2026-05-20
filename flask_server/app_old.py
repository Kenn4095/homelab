from flask import Flask, send_from_directory, render_template, abort, request, jsonify
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


def run_download_in_background(job_id, url, mode):
    """Run yt-dlp in a background thread and track progress."""
    import re
    
    try:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'downloading'
        
        download_dir = AUDIO_DIR if mode == 'audio' else VIDEO_DIR
        output_template = os.path.join(download_dir, '%(title)s [%(id)s].%(ext)s')
        cmd = [sys.executable, '-m', 'yt_dlp', '-o', output_template, '--no-overwrites', '--no-playlist']
        
        if mode == 'audio':
            cmd += ['--extract-audio', '--audio-format', 'mp3']
        else:  # video
            cmd += ['-f', 'bestvideo+bestaudio/best', '--merge-output-format', 'mp4']
        
        cmd.append(url)
        
        # Use Popen with separate thread to read stderr for progress
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line-buffered
            universal_newlines=True
        )
        
        # Regex patterns for yt-dlp progress
        progress_pattern = re.compile(r'\[download\]\s+([\d.]+)%\s+of\s+([\d.]+)([KMG]iB)?')
        
        def read_progress():
            """Read stderr in a thread to capture progress updates."""
            try:
                for line in process.stderr:
                    match = progress_pattern.search(line)
                    if match:
                        percent_str = match.group(1)
                        size_str = match.group(2)
                        unit = match.group(3) or 'B'
                        
                        try:
                            percent = float(percent_str)
                            with jobs_lock:
                                if download_jobs[job_id]['status'] == 'downloading':
                                    download_jobs[job_id]['progress_percent'] = percent
                                    download_jobs[job_id]['total_size_str'] = f"{size_str} {unit}"
                        except ValueError:
                            pass
            except Exception as e:
                print(f"Error reading progress: {e}")
        
        # Start thread to read progress
        progress_thread = threading.Thread(target=read_progress, daemon=True)
        progress_thread.start()
        
        # Wait for process to complete
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = stderr.strip() or stdout.strip()
            return
        
        # Find the latest downloaded file
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
        else:
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = 'Download completed but file not found'
    
    except subprocess.TimeoutExpired:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = 'Download timed out after 1 hour'
    except Exception as e:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = str(e)


@app.route('/')
def index():
    return render_template('index.html')


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
    
    # Clean up completed jobs after returning (optional: keep for a period)
    if job['status'] in ('completed', 'failed'):
        # Could add cleanup logic here
        pass
    
    return jsonify(job)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
