#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import warnings
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import requests
from huggingface_hub import CommitOperationAdd, HfApi

try:
    import rarfile
except ImportError:
    rarfile = None

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="huggingface_hub")

LINKS_FILE = Path("links.txt")
CHUNK = 2 * 1024 * 1024
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 180
RETRIES = 3
MAX_WORKERS = 8
TICK = 0.25

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36"
)

ARCHIVES = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)

TUNNEL_RE = re.compile(
    r"https://[a-zA-Z0-9-]+\.trycloudflare\.com"
)

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width,initial-scale=1,viewport-fit=cover"
>
<meta name="color-scheme" content="dark">
<title>HF Uploader</title>

<style>
:root {
    --bg: #07090d;
    --card: #11161e;
    --line: #252d39;
    --text: #f5f7fb;
    --muted: #8f99a8;
    --violet: #8066ff;
    --green: #26d6aa;
    --red: #ff667f;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    background:
        radial-gradient(
            circle at 8% 0,
            #1a1531 0,
            transparent 34%
        ),
        var(--bg);
    color: var(--text);
    font:
        14px/1.5 Inter,
        system-ui,
        -apple-system,
        "Segoe UI",
        sans-serif;
}

main {
    width: min(1050px, calc(100% - 24px));
    margin: auto;
    padding: 28px 0 48px;
}

.top {
    display: flex;
    justify-content: space-between;
    gap: 18px;
    align-items: flex-start;
    margin-bottom: 20px;
}

.eyebrow,
.muted {
    color: var(--muted);
}

.eyebrow {
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .13em;
    text-transform: uppercase;
}

h1 {
    font-size: clamp(28px, 5vw, 44px);
    letter-spacing: -.045em;
    margin: 3px 0;
}

.live {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 8px 12px;
    background: #0b1016;
    white-space: nowrap;
}

.dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    margin-right: 8px;
    box-shadow: 0 0 0 5px #26d6aa1c;
}

.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
}

.card {
    background:
        linear-gradient(
            180deg,
            #151b24,
            #0e131a
        );
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 18px 60px #0005;
}

.wide {
    grid-column: 1 / -1;
}

.head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}

.head h2 {
    font-size: 15px;
    margin: 0;
}

.pill {
    border: 1px solid var(--line);
    background: #090d12;
    color: var(--muted);
    border-radius: 999px;
    padding: 5px 9px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: .08em;
    text-transform: uppercase;
}

.big {
    font-size: clamp(30px, 6vw, 48px);
    font-weight: 780;
    letter-spacing: -.05em;
    margin-top: 14px;
}

.bar {
    height: 10px;
    border: 1px solid #202735;
    background: #070a0e;
    border-radius: 999px;
    overflow: hidden;
    margin: 16px 0 12px;
}

.fill {
    height: 100%;
    width: 0;
    background:
        linear-gradient(
            90deg,
            var(--violet),
            #ad9cff
        );
    transition: width .2s linear;
}

.upload .fill {
    background:
        linear-gradient(
            90deg,
            var(--green),
            #7eead1
        );
}

.ind {
    width: 35% !important;
    animation: move 1.1s ease-in-out infinite;
}

@keyframes move {
    from {
        transform: translateX(-110%);
    }

    to {
        transform: translateX(315%);
    }
}

.metrics {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
}

.metric,
.file {
    background: #0a0f15;
    border: 1px solid #202834;
    border-radius: 12px;
    padding: 10px;
}

.metric b {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.metric span {
    display: block;
    color: var(--muted);
    font-size: 10px;
    letter-spacing: .07em;
    text-transform: uppercase;
    margin-top: 2px;
}

.files {
    display: grid;
    gap: 8px;
    margin-top: 12px;
}

.file {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
}

.name {
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.small {
    font-size: 12px;
    color: var(--muted);
}

.result {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    padding: 12px 0;
    border-top: 1px solid var(--line);
}

.result:first-child {
    border-top: 0;
}

.result a {
    color: var(--text);
    font-weight: 700;
    text-decoration: none;
    overflow-wrap: anywhere;
}

.ok {
    color: var(--green);
}

.error {
    margin-top: 8px;
    padding: 10px 12px;
    border: 1px solid #ff667f55;
    background: #ff667f12;
    color: #ffd9df;
    border-radius: 12px;
    overflow-wrap: anywhere;
}

.footer {
    text-align: center;
    color: var(--muted);
    font-size: 12px;
    margin-top: 18px;
}

@media (max-width: 740px) {
    main {
        width: calc(100% - 16px);
        padding-top: 16px;
    }

    .top {
        flex-direction: column;
    }

    .grid {
        grid-template-columns: 1fr;
    }

    .wide {
        grid-column: auto;
    }

    .metrics {
        grid-template-columns: 1fr 1fr;
    }

    .metric:last-child {
        grid-column: 1 / -1;
    }

    .card {
        padding: 15px;
    }
}
</style>
</head>

<body>
<main>
    <div class="top">
        <div>
            <div class="eyebrow">
                GitHub Actions · Hugging Face
            </div>

            <h1>Live uploader</h1>

            <div id="repo" class="muted">
                Connecting…
            </div>
        </div>

        <div class="live">
            <span id="dot" class="dot"></span>
            <span id="conn">Live</span>
        </div>
    </div>

    <section class="grid">
        <article class="card">
            <div class="head">
                <h2>Download</h2>
                <span id="ds" class="pill">
                    Waiting
                </span>
            </div>

            <div id="dp" class="big">
                0%
            </div>

            <div id="dsummary" class="muted">
                Waiting for files
            </div>

            <div class="bar">
                <div id="db" class="fill"></div>
            </div>

            <div class="metrics">
                <div class="metric">
                    <b id="dbytes">0 B</b>
                    <span>Transferred</span>
                </div>

                <div class="metric">
                    <b id="dspeed">0 B/s</b>
                    <span>Speed</span>
                </div>

                <div class="metric">
                    <b id="deta">—</b>
                    <span>ETA</span>
                </div>
            </div>

            <div id="files" class="files"></div>
        </article>

        <article class="card upload">
            <div class="head">
                <h2>Upload</h2>
                <span id="us" class="pill">
                    Waiting
                </span>
            </div>

            <div id="up" class="big">
                0%
            </div>

            <div id="ufile" class="muted">
                Waiting for a downloaded file
            </div>

            <div class="bar">
                <div id="ub" class="fill"></div>
            </div>

            <div class="metrics">
                <div class="metric">
                    <b id="ubytes">0 B</b>
                    <span>Processed</span>
                </div>

                <div class="metric">
                    <b id="uspeed">0 B/s</b>
                    <span>Speed</span>
                </div>

                <div class="metric">
                    <b id="ueta">—</b>
                    <span>ETA</span>
                </div>
            </div>
        </article>

        <article class="card wide">
            <div class="head">
                <h2>Uploaded files</h2>

                <span id="count" class="pill">
                    0 complete
                </span>
            </div>

            <div id="results" class="muted">
                Successful links will appear here.
            </div>

            <div id="errors"></div>
        </article>
    </section>

    <div id="phase" class="footer">
        Starting…
    </div>
</main>

<script>
const $ = id => document.getElementById(id);

const formatSize = input => {
    let value = Number(input || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;

    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }

    return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
};

const formatTime = input => {
    if (input == null || !Number.isFinite(input)) {
        return "—";
    }

    const seconds = Math.max(0, Math.round(input));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remaining = seconds % 60;

    if (hours) {
        return (
            `${hours}:` +
            `${String(minutes).padStart(2, "0")}:` +
            `${String(remaining).padStart(2, "0")}`
        );
    }

    return (
        `${minutes}:` +
        `${String(remaining).padStart(2, "0")}`
    );
};

function updateBar(id, percentage, running) {
    const element = $(id);

    if (percentage == null && running) {
        element.classList.add("ind");
        element.style.width = "35%";
        return;
    }

    element.classList.remove("ind");
    element.style.transform = "";
    element.style.width =
        `${Math.max(
            0,
            Math.min(
                100,
                Number(percentage || 0)
            )
        )}%`;
}

function render(state) {
    $("repo").textContent =
        `${state.repo_type}/${state.repo_id}` +
        ` · ${state.branch}` +
        ` · ${state.path || "/"}`;

    $("phase").textContent = state.phase || "";

    const download = state.download || {};

    $("ds").textContent =
        download.status || "waiting";

    $("dp").textContent =
        download.percent == null
            ? "Live"
            : `${Number(download.percent).toFixed(1)}%`;

    $("dsummary").textContent =
        `${download.complete || 0}/` +
        `${download.files || 0} complete` +
        (
            download.failed
                ? ` · ${download.failed} failed`
                : ""
        );

    $("dbytes").textContent =
        download.total
            ? (
                `${formatSize(download.done)} / ` +
                `${formatSize(download.total)}`
            )
            : formatSize(download.done);

    $("dspeed").textContent =
        `${formatSize(download.speed)}/s`;

    $("deta").textContent =
        formatTime(download.eta);

    updateBar(
        "db",
        download.percent,
        download.status === "downloading"
    );

    $("files").innerHTML = "";

    for (const file of download.active || []) {
        const row = document.createElement("div");
        row.className = "file";

        const details = document.createElement("div");
        const name = document.createElement("div");
        const progress = document.createElement("div");
        const status = document.createElement("div");

        name.className = "name";
        name.textContent = file.name;

        progress.className = "small";
        progress.textContent =
            file.total
                ? (
                    `${formatSize(file.done)} / ` +
                    `${formatSize(file.total)}`
                )
                : formatSize(file.done);

        status.className = "small";
        status.textContent = file.status;

        details.append(name, progress);
        row.append(details, status);
        $("files").append(row);
    }

    const upload = state.upload || {};

    $("us").textContent =
        upload.status || "waiting";

    $("up").textContent =
        upload.percent == null
            ? "Live"
            : `${Number(upload.percent).toFixed(1)}%`;

    $("ufile").textContent =
        upload.file ||
        "Waiting for a downloaded file";

    $("ubytes").textContent =
        upload.total
            ? (
                `${formatSize(upload.done)} / ` +
                `${formatSize(upload.total)}`
            )
            : formatSize(upload.done);

    $("uspeed").textContent =
        `${formatSize(upload.speed)}/s`;

    $("ueta").textContent =
        formatTime(upload.eta);

    updateBar(
        "ub",
        upload.percent,
        ["preparing", "uploading"].includes(
            upload.status
        )
    );

    const results = state.results || [];

    $("count").textContent =
        `${results.length} complete`;

    $("results").innerHTML = "";

    if (!results.length) {
        $("results").textContent =
            "Successful links will appear here.";
    }

    for (const result of results) {
        const row = document.createElement("div");
        const link = document.createElement("a");
        const complete = document.createElement("span");

        row.className = "result";

        link.href = result.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = result.file;

        complete.className = "ok";
        complete.textContent = "Complete";

        row.append(link, complete);
        $("results").append(row);
    }

    $("errors").innerHTML = "";

    for (const message of state.errors || []) {
        const error = document.createElement("div");
        error.className = "error";
        error.textContent = message;
        $("errors").append(error);
    }
}

const stream = new EventSource("/events");

stream.onopen = () => {
    $("conn").textContent = "Live";
    $("dot").style.background = "var(--green)";
};

stream.onmessage = event => {
    render(JSON.parse(event.data));
};

stream.onerror = () => {
    $("conn").textContent = "Reconnecting";
    $("dot").style.background = "#ffbf5c";
};

setInterval(() => {
    fetch("/state", {
        cache: "no-store"
    })
        .then(response => response.json())
        .then(render)
        .catch(() => {});
}, 5000);
</script>
</body>
</html>'''


def size_text(value: int | float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(max(value, 0))
    index = 0

    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1

    return f"{number:.1f} {units[index]}"


def clean_name(name: str) -> str:
    name = urllib.parse.unquote(name or "")
    name = os.path.basename(
        name.replace("\\", "/")
    )

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name
    ).strip(". ")

    return name or f"file_{int(time.time())}.bin"


def safe_error(value: BaseException | str) -> str:
    text = (
        str(value).strip() or
        value.__class__.__name__
    )

    text = re.sub(
        r"https?://\S+",
        "[remote URL]",
        text
    )

    token = os.environ.get("HF_TOKEN", "")

    if token:
        text = text.replace(
            token,
            "[redacted]"
        )

    return text[:500]


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    path = folder / clean_name(name)

    if not path.exists():
        return path

    counter = 1
    stem = path.stem
    suffix = path.suffix

    while (
        folder /
        f"{stem}_{counter}{suffix}"
    ).exists():
        counter += 1

    return (
        folder /
        f"{stem}_{counter}{suffix}"
    )


@dataclass(frozen=True)
class Task:
    index: int
    url: str
    name: str | None
    unzip: bool


@dataclass
class Downloaded:
    task: Task
    work: Path
    path: Path | None = None
    error: str = ""


@dataclass
class Result:
    ok: bool
    file: str
    size: int = 0
    url: str = ""
    error: str = ""


class Store:
    def __init__(
        self,
        repo: str,
        repo_type: str,
        branch: str,
        path: str
    ) -> None:
        self.condition = threading.Condition()
        self.version = 1

        self.data: dict[str, Any] = {
            "repo_id": repo,
            "repo_type": repo_type,
            "branch": branch,
            "path": path,
            "phase": "Starting…",
            "download": {
                "status": "waiting",
                "files": 0,
                "complete": 0,
                "failed": 0,
                "done": 0,
                "total": 0,
                "speed": 0,
                "eta": None,
                "percent": 0,
                "active": []
            },
            "upload": {
                "status": "waiting",
                "file": "",
                "done": 0,
                "total": 0,
                "speed": 0,
                "eta": None,
                "percent": 0
            },
            "results": [],
            "errors": []
        }

    def set(
        self,
        key: str,
        value: Any
    ) -> None:
        with self.condition:
            self.data[key] = value
            self.version += 1
            self.condition.notify_all()

    def phase(
        self,
        text: str
    ) -> None:
        self.set(
            "phase",
            text
        )

    def result(
        self,
        item: Result
    ) -> None:
        with self.condition:
            self.data["results"].append({
                "file": item.file,
                "size": item.size,
                "url": item.url
            })

            self.version += 1
            self.condition.notify_all()

    def error(
        self,
        text: str
    ) -> None:
        with self.condition:
            self.data["errors"].append(text)
            self.version += 1
            self.condition.notify_all()

    def snapshot(
        self,
        after: int = -1,
        timeout: float = 0
    ) -> tuple[int, dict[str, Any]]:
        with self.condition:
            if after >= 0:
                self.condition.wait_for(
                    lambda: self.version != after,
                    timeout=timeout
                )

            return (
                self.version,
                copy.deepcopy(self.data)
            )


def handler_for(
    store: Store
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(
            self,
            _format: str,
            *_arguments: Any
        ) -> None:
            pass

        def reply(
            self,
            body: bytes,
            content_type: str
        ) -> None:
            self.send_response(200)

            self.send_header(
                "Content-Type",
                content_type
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Cache-Control",
                "no-store"
            )

            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(
                self.path
            ).path

            if path == "/":
                self.reply(
                    PAGE.encode(),
                    "text/html; charset=utf-8"
                )
                return

            if path == "/state":
                _, data = store.snapshot()

                self.reply(
                    json.dumps(
                        data,
                        separators=(",", ":")
                    ).encode(),
                    "application/json"
                )
                return

            if path == "/health":
                self.reply(
                    b"ok",
                    "text/plain"
                )
                return

            if path != "/events":
                self.send_error(404)
                return

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/event-stream"
            )

            self.send_header(
                "Cache-Control",
                "no-cache, no-transform"
            )

            self.send_header(
                "Connection",
                "keep-alive"
            )

            self.send_header(
                "X-Accel-Buffering",
                "no"
            )

            self.end_headers()

            version = 0

            try:
                while True:
                    current, data = store.snapshot(
                        version,
                        12
                    )

                    if current == version:
                        payload = b": ping\n\n"
                    else:
                        encoded = json.dumps(
                            data,
                            separators=(",", ":")
                        )

                        payload = (
                            f"id: {current}\n"
                            f"data: {encoded}\n\n"
                        ).encode()

                    self.wfile.write(payload)
                    self.wfile.flush()
                    version = current

            except (
                BrokenPipeError,
                ConnectionResetError,
                TimeoutError
            ):
                pass

    return Handler


class Dashboard:
    def __init__(
        self,
        store: Store,
        port: int
    ) -> None:
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            handler_for(store)
        )

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Tunnel:
    def __init__(
        self,
        port: int
    ) -> None:
        self.port = port
        self.process: subprocess.Popen[str] | None = None
        self.url = ""
        self.ready = threading.Event()

    def start(self) -> str:
        binary = shutil.which("cloudflared")

        if not binary:
            raise RuntimeError(
                "cloudflared is not installed"
            )

        self.process = subprocess.Popen(
            [
                binary,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{self.port}",
                "--no-autoupdate",
                "--loglevel",
                "info"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        def read_output() -> None:
            assert self.process
            assert self.process.stdout

            for line in self.process.stdout:
                match = TUNNEL_RE.search(line)

                if match and not self.url:
                    self.url = match.group(0)
                    self.ready.set()

        threading.Thread(
            target=read_output,
            daemon=True
        ).start()

        if not self.ready.wait(35):
            raise RuntimeError(
                "Cloudflare Quick Tunnel "
                "did not return a URL"
            )

        return self.url

    def stop(self) -> None:
        if (
            self.process and
            self.process.poll() is None
        ):
            self.process.terminate()

            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def read_tasks() -> list[Task]:
    if not LINKS_FILE.is_file():
        raise SystemExit(
            "INPUT ERROR | links.txt is missing"
        )

    tasks: list[Task] = []

    for line_number, line in enumerate(
        LINKS_FILE.read_text(
            encoding="utf-8"
        ).splitlines(),
        1
    ):
        raw = line.strip()

        if (
            not raw or
            raw.startswith("#")
        ):
            continue

        unzip = raw.lower().endswith(
            " -unzip"
        )

        if unzip:
            raw = raw[:-7].strip()

        custom_name = None

        if " -n " in raw:
            raw, custom_name = raw.split(
                " -n ",
                1
            )

            custom_name = clean_name(
                custom_name.strip()
            )

        if not raw.startswith(
            ("http://", "https://")
        ):
            raise SystemExit(
                "INPUT ERROR | "
                f"links.txt line {line_number}: "
                "invalid URL"
            )

        tasks.append(
            Task(
                index=len(tasks) + 1,
                url=raw.strip(),
                name=custom_name,
                unzip=unzip
            )
        )

    if not tasks:
        raise SystemExit(
            "INPUT ERROR | "
            "links.txt contains no valid URLs"
        )

    return tasks


class DownloadProgress:
    def __init__(
        self,
        store: Store,
        tasks: list[Task]
    ) -> None:
        self.store = store
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

        self.rows = {
            task.index: {
                "name": (
                    task.name or
                    f"File {task.index}"
                ),
                "done": 0,
                "total": 0,
                "status": "queued"
            }
            for task in tasks
        }

        self.last_at = time.monotonic()
        self.last_bytes = 0
        self.speed = 0.0

        self.thread = threading.Thread(
            target=self.loop,
            daemon=True
        )

    def start(self) -> None:
        self.thread.start()
        self.publish()

    def update(
        self,
        task: Task,
        name: str,
        done: int,
        total: int,
        status: str
    ) -> None:
        with self.lock:
            self.rows[task.index] = {
                "name": name,
                "done": max(done, 0),
                "total": max(total, 0),
                "status": status
            }

    def loop(self) -> None:
        while not self.stop_event.wait(TICK):
            self.publish()

    def publish(self) -> None:
        with self.lock:
            rows = copy.deepcopy(
                self.rows
            )

        now = time.monotonic()

        done = sum(
            int(item["done"])
            for item in rows.values()
        )

        elapsed = max(
            now - self.last_at,
            0.001
        )

        instant_speed = max(
            done - self.last_bytes,
            0
        ) / elapsed

        if self.speed:
            self.speed = (
                self.speed * 0.65 +
                instant_speed * 0.35
            )
        else:
            self.speed = instant_speed

        self.last_at = now
        self.last_bytes = done

        complete = sum(
            item["status"] == "complete"
            for item in rows.values()
        )

        failed = sum(
            item["status"] == "failed"
            for item in rows.values()
        )

        totals_known = all(
            int(item["total"]) > 0
            for item in rows.values()
        )

        total = (
            sum(
                int(item["total"])
                for item in rows.values()
            )
            if totals_known
            else 0
        )

        percent = (
            min(
                done / total * 100,
                100
            )
            if total
            else None
        )

        active = [
            item
            for item in rows.values()
            if item["status"] in {
                "downloading",
                "retrying"
            }
        ]

        if complete + failed == len(rows):
            status = (
                "complete"
                if not failed
                else "partial"
            )
        elif active:
            status = "downloading"
        else:
            status = "waiting"

        eta = (
            (total - done) / self.speed
            if total and self.speed
            else None
        )

        self.store.set(
            "download",
            {
                "status": status,
                "files": len(rows),
                "complete": complete,
                "failed": failed,
                "done": done,
                "total": total,
                "speed": int(self.speed),
                "eta": eta,
                "percent": percent,
                "active": active
            }
        )

    def finish(
        self
    ) -> tuple[int, int, int]:
        self.stop_event.set()
        self.thread.join(1)
        self.publish()

        with self.lock:
            rows = copy.deepcopy(
                self.rows
            )

        complete = sum(
            item["status"] == "complete"
            for item in rows.values()
        )

        failed = sum(
            item["status"] == "failed"
            for item in rows.values()
        )

        downloaded = sum(
            int(item["done"])
            for item in rows.values()
        )

        return complete, failed, downloaded


def response_name(
    response: requests.Response
) -> str:
    disposition = response.headers.get(
        "content-disposition",
        ""
    )

    patterns = (
        r"filename\*=UTF-8''([^;]+)",
        r'filename="([^"]+)"',
        r"filename=([^;]+)"
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            disposition,
            re.IGNORECASE
        )

        if match:
            return clean_name(
                match.group(1).strip(" '\"")
            )

    name = clean_name(
        Path(
            urllib.parse.urlparse(
                response.url
            ).path
        ).name
    )

    if "." in name:
        return name

    return f"download_{int(time.time())}.bin"


def response_size(
    response: requests.Response,
    existing: int
) -> int:
    match = re.search(
        r"/(\d+)$",
        response.headers.get(
            "content-range",
            ""
        )
    )

    if match:
        return int(match.group(1))

    length = int(
        response.headers.get(
            "content-length",
            "0"
        ) or 0
    )

    if response.status_code == 206:
        return existing + length

    return length


def download(
    task: Task,
    progress: DownloadProgress
) -> Downloaded:
    work = Path(
        tempfile.mkdtemp(
            prefix=f"hf_{task.index}_"
        )
    )

    path: Path | None = None
    final_error = "unknown download error"

    for attempt in range(
        1,
        RETRIES + 1
    ):
        try:
            existing = (
                path.stat().st_size
                if path and path.exists()
                else 0
            )

            headers = {
                "User-Agent": USER_AGENT
            }

            if existing:
                headers["Range"] = (
                    f"bytes={existing}-"
                )

            displayed_name = (
                path.name
                if path
                else task.name or
                f"File {task.index}"
            )

            progress.update(
                task,
                displayed_name,
                existing,
                0,
                "downloading"
            )

            with requests.get(
                task.url,
                headers=headers,
                stream=True,
                allow_redirects=True,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT
                )
            ) as response:
                if (
                    response.status_code == 416 and
                    path and
                    path.exists()
                ):
                    match = re.search(
                        r"\*/(\d+)$",
                        response.headers.get(
                            "content-range",
                            ""
                        )
                    )

                    if (
                        match and
                        path.stat().st_size ==
                        int(match.group(1))
                    ):
                        size = path.stat().st_size

                        progress.update(
                            task,
                            path.name,
                            size,
                            size,
                            "complete"
                        )

                        return Downloaded(
                            task,
                            work,
                            path
                        )

                    path.unlink(
                        missing_ok=True
                    )

                    raise RuntimeError(
                        "partial download "
                        "could not be resumed"
                    )

                response.raise_for_status()

                if path is None:
                    path = unique_path(
                        work / "downloads",
                        task.name or
                        response_name(response)
                    )

                    existing = 0

                resumed = (
                    existing > 0 and
                    response.status_code == 206
                )

                if not resumed:
                    existing = 0

                total = response_size(
                    response,
                    existing
                )

                done = existing

                progress.update(
                    task,
                    path.name,
                    done,
                    total,
                    "downloading"
                )

                mode = (
                    "ab"
                    if resumed
                    else "wb"
                )

                with path.open(mode) as handle:
                    for block in response.iter_content(
                        CHUNK
                    ):
                        if not block:
                            continue

                        handle.write(block)
                        done += len(block)

                        progress.update(
                            task,
                            path.name,
                            done,
                            total,
                            "downloading"
                        )

                final_size = path.stat().st_size

                if (
                    not final_size or
                    total and
                    final_size < total
                ):
                    raise RuntimeError(
                        "download ended before "
                        "the file was complete"
                    )

                progress.update(
                    task,
                    path.name,
                    final_size,
                    total or final_size,
                    "complete"
                )

                return Downloaded(
                    task,
                    work,
                    path
                )

        except Exception as exc:
            final_error = safe_error(exc)

            displayed_name = (
                path.name
                if path
                else task.name or
                f"File {task.index}"
            )

            done = (
                path.stat().st_size
                if path and path.exists()
                else 0
            )

            progress.update(
                task,
                displayed_name,
                done,
                0,
                (
                    "retrying"
                    if attempt < RETRIES
                    else "failed"
                )
            )

            if attempt < RETRIES:
                time.sleep(
                    min(
                        2 ** (attempt - 1),
                        4
                    )
                )

    return Downloaded(
        task,
        work,
        path,
        final_error
    )


def extract(
    path: Path,
    target: Path
) -> list[Path]:
    target.mkdir(
        parents=True,
        exist_ok=True
    )

    lower = path.name.lower()

    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)

    elif lower.endswith(".rar"):
        if rarfile is None:
            raise RuntimeError(
                "RAR support is unavailable"
            )

        with rarfile.RarFile(path) as archive:
            archive.extractall(target)

    elif lower.endswith((
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tbz2",
        ".tar.xz",
        ".txz"
    )):
        with tarfile.open(
            path,
            "r:*"
        ) as archive:
            archive.extractall(
                target,
                filter="data"
            )

    elif lower.endswith(".7z"):
        subprocess.run(
            [
                "7z",
                "x",
                "-y",
                f"-o{target}",
                str(path)
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

    else:
        raise RuntimeError(
            "unsupported archive type"
        )

    return sorted(
        [
            item
            for item in target.rglob("*")
            if (
                item.is_file() and
                not item.name.startswith(".")
            )
        ],
        key=lambda item: item.name.lower()
    )


class UploadProgress:
    def __init__(
        self,
        store: Store,
        path: Path
    ) -> None:
        self.store = store
        self.path = path
        self.total = path.stat().st_size
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

        self.status = "preparing"
        self.done = 0
        self.speed = 0.0
        self.last_at = time.monotonic()
        self.last_done = 0

        self.thread = threading.Thread(
            target=self.loop,
            daemon=True
        )

    def start(self) -> None:
        self.thread.start()
        self.publish()

    def phase(
        self,
        status: str
    ) -> None:
        with self.lock:
            self.status = status
            self.done = 0
            self.speed = 0.0
            self.last_at = time.monotonic()
            self.last_done = 0

        self.publish()

    def update(
        self,
        position: int
    ) -> None:
        with self.lock:
            self.done = max(
                self.done,
                min(
                    position,
                    self.total
                )
            )

    def loop(self) -> None:
        while not self.stop_event.wait(TICK):
            self.publish()

    def publish(self) -> None:
        with self.lock:
            status = self.status
            done = self.done

        now = time.monotonic()

        elapsed = max(
            now - self.last_at,
            0.001
        )

        instant_speed = max(
            done - self.last_done,
            0
        ) / elapsed

        if self.speed:
            self.speed = (
                self.speed * 0.65 +
                instant_speed * 0.35
            )
        else:
            self.speed = instant_speed

        self.last_at = now
        self.last_done = done

        eta = (
            (self.total - done) /
            self.speed
            if self.speed
            else None
        )

        percent = (
            done / self.total * 100
            if self.total
            else 100
        )

        self.store.set(
            "upload",
            {
                "status": status,
                "file": self.path.name,
                "done": done,
                "total": self.total,
                "speed": int(self.speed),
                "eta": eta,
                "percent": percent
            }
        )

    def finish(
        self,
        status: str
    ) -> None:
        with self.lock:
            self.status = status

            if status == "complete":
                self.done = self.total

        self.stop_event.set()
        self.thread.join(1)
        self.publish()


class TrackedFile(io.BufferedReader):
    def __init__(
        self,
        path: Path,
        callback: Callable[[int], None]
    ) -> None:
        raw = open(
            path,
            "rb",
            buffering=0
        )

        super().__init__(
            raw,
            buffer_size=CHUNK
        )

        self.callback = callback

    def report(
        self,
        amount: int
    ) -> None:
        if amount > 0:
            self.callback(
                self.tell()
            )

    def read(
        self,
        size: int = -1
    ) -> bytes:
        data = super().read(size)
        self.report(len(data))
        return data

    def read1(
        self,
        size: int = -1
    ) -> bytes:
        data = super().read1(size)
        self.report(len(data))
        return data

    def readinto(
        self,
        buffer: Any
    ) -> int:
        amount = super().readinto(buffer)
        self.report(amount or 0)
        return amount


class Hub:
    def __init__(
        self,
        store: Store,
        repo: str,
        path: str,
        branch: str,
        repo_type: str
    ) -> None:
        self.store = store
        self.repo = repo
        self.path = path.strip("/")
        self.branch = branch or "main"
        self.repo_type = repo_type

        self.token = os.environ.get(
            "HF_TOKEN",
            ""
        ).strip()

        if not self.token:
            raise SystemExit(
                "CONFIG ERROR | "
                "HF_TOKEN secret is missing"
            )

        if "/" not in self.repo:
            raise SystemExit(
                "CONFIG ERROR | "
                "repository must use "
                "owner/repository format"
            )

        self.api = HfApi(
            token=self.token
        )

        self.api.create_repo(
            repo_id=self.repo,
            repo_type=self.repo_type,
            token=self.token,
            exist_ok=True
        )

        if self.branch != "main":
            references = self.api.list_repo_refs(
                repo_id=self.repo,
                repo_type=self.repo_type,
                token=self.token
            )

            existing = {
                reference.name
                for reference in references.branches
            }

            if self.branch not in existing:
                self.api.create_branch(
                    repo_id=self.repo,
                    repo_type=self.repo_type,
                    branch=self.branch,
                    token=self.token,
                    exist_ok=True
                )

    def remote(
        self,
        path: Path
    ) -> str:
        filename = clean_name(path.name)

        if self.path:
            return (
                f"{self.path}/"
                f"{filename}"
            )

        return filename

    def url(
        self,
        remote: str
    ) -> str:
        prefix = (
            "datasets/"
            if self.repo_type == "dataset"
            else ""
        )

        revision = urllib.parse.quote(
            self.branch,
            safe=""
        )

        encoded_path = urllib.parse.quote(
            remote,
            safe="/"
        )

        return (
            "https://huggingface.co/"
            f"{prefix}{self.repo}/"
            f"blob/{revision}/"
            f"{encoded_path}"
        )

    def upload(
        self,
        path: Path
    ) -> Result:
        remote = self.remote(path)

        progress = UploadProgress(
            self.store,
            path
        )

        progress.start()

        self.store.phase(
            f"Preparing {path.name}"
        )

        try:
            with TrackedFile(
                path,
                progress.update
            ) as reader:
                operation = CommitOperationAdd(
                    path_in_repo=remote,
                    path_or_fileobj=reader
                )

                progress.phase("uploading")

                self.store.phase(
                    f"Uploading {path.name}"
                )

                self.api.create_commit(
                    repo_id=self.repo,
                    repo_type=self.repo_type,
                    revision=self.branch,
                    operations=[operation],
                    commit_message=(
                        f"Upload {path.name}"
                    ),
                    token=self.token,
                    num_threads=1
                )

            progress.finish("complete")

            result = Result(
                ok=True,
                file=path.name,
                size=path.stat().st_size,
                url=self.url(remote)
            )

            self.store.result(result)
            return result

        except Exception as exc:
            error = safe_error(exc)
            progress.finish("failed")

            self.store.error(
                f"{path.name}: {error}"
            )

            return Result(
                ok=False,
                file=path.name,
                error=error
            )


def write_summary(
    results: list[Result],
    hub: Hub
) -> None:
    successful = [
        result
        for result in results
        if result.ok
    ]

    failed = [
        result
        for result in results
        if not result.ok
    ]

    lines = [
        "# Hugging Face Upload Summary",
        "",
        f"- Repository: `{hub.repo}`",
        f"- Type: `{hub.repo_type}`",
        f"- Branch: `{hub.branch}`",
        f"- Destination: `{hub.path or '/'}`",
        f"- Successful: **{len(successful)}**",
        f"- Failed: **{len(failed)}**",
        ""
    ]

    if successful:
        lines.extend([
            "## Uploaded",
            ""
        ])

        lines.extend(
            (
                f"- [{result.file}]"
                f"({result.url}) — "
                f"{size_text(result.size)}"
            )
            for result in successful
        )

        lines.append("")

    if failed:
        lines.extend([
            "## Failed",
            ""
        ])

        lines.extend(
            (
                f"- `{result.file}` — "
                f"{result.error}"
            )
            for result in failed
        )

        lines.append("")

    text = "\n".join(lines)

    Path(
        "upload_summary.md"
    ).write_text(
        text + "\n",
        encoding="utf-8"
    )

    step_summary = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if step_summary:
        with open(
            step_summary,
            "a",
            encoding="utf-8"
        ) as handle:
            handle.write(
                text + "\n"
            )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--repo-id",
        required=True
    )

    parser.add_argument(
        "--path-in-repo",
        default=""
    )

    parser.add_argument(
        "--branch",
        default="main"
    )

    parser.add_argument(
        "--repo-type",
        choices=(
            "model",
            "dataset"
        ),
        default="model"
    )

    parser.add_argument(
        "--download-workers",
        type=int,
        default=3
    )

    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8787
    )

    return parser.parse_args()


def main() -> int:
    args = arguments()

    repo = args.repo_id.strip()
    path = args.path_in_repo.strip("/")
    branch = args.branch.strip() or "main"
    repo_type = args.repo_type

    workers = max(
        1,
        min(
            args.download_workers,
            MAX_WORKERS
        )
    )

    port = max(
        1024,
        min(
            args.dashboard_port,
            65535
        )
    )

    tasks = read_tasks()

    store = Store(
        repo,
        repo_type,
        branch,
        path
    )

    dashboard = Dashboard(
        store,
        port
    )

    tunnel = Tunnel(port)
    dashboard.start()

    try:
        try:
            dashboard_url = tunnel.start()

            print(
                "DOWNLOAD  LIVE | "
                f"{dashboard_url}",
                flush=True
            )

        except Exception as exc:
            store.error(
                "Dashboard unavailable: "
                f"{safe_error(exc)}"
            )

            print(
                "DOWNLOAD  LIVE | "
                "dashboard unavailable; continuing",
                flush=True
            )

        store.phase(
            "Connecting to Hugging Face"
        )

        hub = Hub(
            store,
            repo,
            path,
            branch,
            repo_type
        )

        progress = DownloadProgress(
            store,
            tasks
        )

        progress.start()

        results: list[Result] = []

        store.phase(
            f"Downloading {len(tasks)} file(s) "
            f"with {workers} worker(s)"
        )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers
        ) as pool:
            futures = [
                pool.submit(
                    download,
                    task,
                    progress
                )
                for task in tasks
            ]

            for future in concurrent.futures.as_completed(
                futures
            ):
                item = future.result()

                try:
                    if item.error or not item.path:
                        name = (
                            item.task.name or
                            f"File {item.task.index}"
                        )

                        error = (
                            item.error or
                            "download failed"
                        )

                        store.error(
                            f"{name}: {error}"
                        )

                        results.append(
                            Result(
                                ok=False,
                                file=name,
                                error=error
                            )
                        )

                        continue

                    files = [item.path]

                    if item.task.unzip:
                        store.phase(
                            f"Extracting "
                            f"{item.path.name}"
                        )

                        if not (
                            item.path.name
                            .lower()
                            .endswith(ARCHIVES)
                        ):
                            raise RuntimeError(
                                "-unzip was requested "
                                "for a non-archive file"
                            )

                        files = extract(
                            item.path,
                            item.work / "extracted"
                        )

                        if not files:
                            raise RuntimeError(
                                "archive contains "
                                "no uploadable files"
                            )

                    for file in files:
                        results.append(
                            hub.upload(file)
                        )

                except Exception as exc:
                    name = (
                        item.path.name
                        if item.path
                        else f"File {item.task.index}"
                    )

                    error = safe_error(exc)

                    store.error(
                        f"{name}: {error}"
                    )

                    results.append(
                        Result(
                            ok=False,
                            file=name,
                            error=error
                        )
                    )

                finally:
                    shutil.rmtree(
                        item.work,
                        ignore_errors=True
                    )

        _, download_failed, _ = (
            progress.finish()
        )

        successful = [
            result
            for result in results
            if result.ok
        ]

        failed = [
            result
            for result in results
            if not result.ok
        ]

        store.phase(
            "All work completed"
            if (
                not failed and
                not download_failed
            )
            else "Completed with errors"
        )

        write_summary(
            results,
            hub
        )

        uploaded_size = sum(
            result.size
            for result in successful
        )

        print(
            "UPLOAD    "
            f"{'COMPLETE' if not failed else 'PARTIAL'}"
            " | "
            f"{len(successful)} successful"
            " | "
            f"{len(failed)} failed"
            " | "
            f"{size_text(uploaded_size)}",
            flush=True
        )

        if len(successful) == 1:
            print(
                "SUCCESS   "
                f"{successful[0].url}",
                flush=True
            )

        elif successful:
            print(
                "SUCCESS   "
                f"{len(successful)} links are "
                "listed in the job summary",
                flush=True
            )

        else:
            print(
                "SUCCESS   no files uploaded",
                flush=True
            )

        time.sleep(1)

        return (
            0
            if (
                successful and
                not failed and
                not download_failed
            )
            else 1
        )

    finally:
        tunnel.stop()
        dashboard.stop()


if __name__ == "__main__":
    raise SystemExit(main())
