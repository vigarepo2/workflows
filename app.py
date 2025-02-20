from flask import Flask, send_from_directory, jsonify, request, Response
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
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>File Server</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                text-align: center;
                background-color: #f4f4f4;
                margin: 0;
                padding: 20px;
            }}
            h1 {{
                color: #333;
            }}
            table {{
                width: 80%;
                margin: auto;
                border-collapse: collapse;
                background: white;
            }}
            th, td {{
                padding: 10px;
                border: 1px solid #ddd;
            }}
            th {{
                background-color: #007bff;
                color: white;
            }}
            a {{
                text-decoration: none;
                color: #007bff;
                font-weight: bold;
            }}
            video {{
                width: 80%;
                margin-top: 20px;
                border-radius: 8px;
                box-shadow: 0px 0px 10px rgba(0, 0, 0, 0.1);
            }}
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
            {''.join(f'<tr><td>{file}</td><td><a href="{base_url}/download/{file}" download>Download</a></td><td><a href="{base_url}/stream/{file}">Stream</a></td></tr>' for file in files)}
        </table>
    </body>
    </html>
    """

# List files (API)
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

# Stream video with subtitles & multiple audio tracks
@app.route("/stream/<filename>")
def stream_video(filename):
    file_path = os.path.join(FILES_DIR, filename)
    
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    command = [
        "ffmpeg",
        "-i", file_path,
        "-movflags", "frag_keyframe+empty_moov",
        "-c:v", "copy",
        "-c:a", "aac",
        "-f", "mp4",
        "pipe:1"
    ]

    def generate():
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        while True:
            chunk = process.stdout.read(1024)
            if not chunk:
                break
            yield chunk
        process.stdout.close()
        process.wait()

    return Response(generate(), mimetype="video/mp4")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
