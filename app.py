from flask import Flask, send_from_directory, jsonify
import os

app = Flask(__name__)

# Directory where downloaded files are stored
FILES_DIR = "files"

# Serve files from 'files' directory
@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(FILES_DIR, filename, as_attachment=True)

# Show only downloaded files
@app.route("/db")
def list_files():
    files = os.listdir(FILES_DIR)
    return jsonify(files)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
