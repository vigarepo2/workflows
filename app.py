from flask import Flask, send_from_directory, jsonify, request, Response, render_template_string
import os
import subprocess

app = Flask(__name__)

FILES_DIR = "/app/files"

# Get base URL dynamically
def get_base_url():
    return request.host_url.rstrip('/')

# Home Page - Lists Files & Shows UI
@app.route("/")
def home():
    files = os.listdir(FILES_DIR)
    base_url = get_base_url()
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>File Server</title>
        <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; background-color: #f4f4f4; margin: 0; padding: 20px; }
            h1 { color: #333; }
            table { width: 80%; margin: auto; border-collapse: collapse; background: white; }
            th, td { padding: 10px; border: 1px solid #ddd; }
            th { background-color: #007bff; color: white; }
            a { text-decoration: none; color: #007bff; font-weight: bold; }
            video { width: 80%; margin-top: 20px; border-radius: 8px; box-shadow: 0px 0px 10px rgba(0, 0, 0, 0.1); }
        </style>
    </head>
    <body>
        <h1>File Server</h1>
        <table>
            <tr>
                <th>Filename</th>
                <th>Download</th>
                <th>Stream</th>
            </tr>
            {% for file in files %}
                <tr>
                    <td>{{ file }}</td>
                    <td><a href="{{ base_url }}/download/{{ file }}" download>Download</a></td>
                    <td><a href="{{ base_url }}/stream/{{ file }}">Stream</a></td>
                </tr>
            {% endfor %}
        </table>

        <h2>Video Preview</h2>
        <video id="video" controls></video>
        <script>
            function loadVideo(url) {
                var video = document.getElementById('video');
                if (HLS.isSupported()) {
                    var hls = new HLS();
                    hls.loadSource(url);
                    hls.attachMedia(video);
                } else {
                    video.src = url;
                }
            }
        </script>
    </body>
    </html>
    """, files=files, base_url=base_url)

# List files API
@app.route("/db")
def list_files():
    files = os.listdir(FILES_DIR)
    file_links = [
        {
            "filename": f,
            "download_url": f"{get_base_url()}/download/{f}",
            "stream_url": f"{get_base_url()}/stream/{f}"
        }
        for f in files
    ]
    return jsonify(file_links)

# Direct file download
@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(FILES_DIR, filename, as_attachment=True)

# Stream video using HLS
@app.route("/stream/<filename>")
def stream_video(filename):
    file_path = os.path.join(FILES_DIR, filename)
    hls_dir = os.path.join(FILES_DIR, "hls", filename)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    # Create HLS folder if not exists
    os.makedirs(hls_dir, exist_ok=True)

    # Convert video to HLS format on-demand
    hls_playlist = os.path.join(hls_dir, "playlist.m3u8")
    if not os.path.exists(hls_playlist):
        command = [
            "ffmpeg", "-i", file_path,
            "-preset", "ultrafast", "-tune", "zerolatency",
            "-c:v", "libx264", "-crf", "23", "-c:a", "aac",
            "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", "5", "-hls_list_size", "0",
            "-hls_flags", "delete_segments",
            "-hls_segment_filename", os.path.join(hls_dir, "segment_%03d.ts"),
            hls_playlist
        ]
        subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return jsonify({"hls_url": f"{get_base_url()}/hls/{filename}/playlist.m3u8"})

# Serve HLS segments
@app.route("/hls/<filename>/<segment>")
def serve_hls(filename, segment):
    return send_from_directory(os.path.join(FILES_DIR, "hls", filename), segment)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
