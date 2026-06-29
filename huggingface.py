#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")

import requests
from huggingface_hub import HfApi, login

try:
    from huggingface_hub.utils import disable_progress_bars
    disable_progress_bars()
except Exception:
    pass

try:
    import rarfile
except Exception:
    rarfile = None

try:
    import vars as config
except Exception as exc:
    raise SystemExit(f"CONFIG ERROR | vars.py missing or invalid: {exc}") from exc

ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class Log:
    lock = threading.Lock()

    @classmethod
    def line(cls, text: str) -> None:
        with cls.lock:
            print(text, flush=True)

    @classmethod
    def section(cls, title: str) -> None:
        with cls.lock:
            print(f"\n── {title} ──", flush=True)


def cfg(name: str, default: Any = None) -> Any:
    return getattr(config, name, default)


def env_or_cfg(env_name: str, cfg_name: str, default: Any = "") -> str:
    value = os.environ.get(env_name)
    if value is None or str(value).strip() == "":
        value = cfg(cfg_name, default)
    return str(value).strip()


def require_token() -> str:
    token_env_name = str(cfg("HF_TOKEN_ENV", "HF_TOKEN")).strip() or "HF_TOKEN"
    token = os.environ.get(token_env_name, "").strip()
    if not token and token_env_name != "HF_WRITE_TOKEN":
        token = os.environ.get("HF_WRITE_TOKEN", "").strip()
    if not token:
        raise SystemExit(f"CONFIG ERROR | Add Hugging Face token as GitHub secret: {token_env_name}")
    return token


def require_authorized() -> None:
    workflow_confirmed = os.environ.get("CONFIRM_RIGHTS", "").strip().lower() == "true"
    config_confirmed = bool(cfg("AUTHORIZED_ONLY", False))
    if not workflow_confirmed and not config_confirmed:
        raise SystemExit("CONFIG ERROR | Set AUTHORIZED_ONLY=True in vars.py for files you own or have permission to upload.")


def as_int(value: Any, default: int, minimum: int = 1, maximum: int = 10) -> int:
    try:
        number = int(str(value).strip())
    except Exception:
        number = default
    return max(minimum, min(number, maximum))


def format_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes or 0)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


def sanitize_filename(filename: str) -> str:
    filename = urllib.parse.unquote(filename or "")
    filename = os.path.basename(filename.replace("\\", "/"))
    filename = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", filename).strip(". ")
    return filename or f"file_{int(time.time())}.bin"


def unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / sanitize_filename(filename)
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        next_candidate = folder / f"{stem}_{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    unzip = False
    work = raw
    if work.lower().endswith(" -unzip"):
        unzip = True
        work = work[: -len(" -unzip")].strip()

    custom_filename = None
    if " -n " in work:
        url, custom_filename = work.split(" -n ", 1)
        custom_filename = sanitize_filename(custom_filename.strip())
    else:
        url = work

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"invalid URL: {raw}")

    return {"raw": raw, "url": url, "custom_filename": custom_filename, "unzip": unzip}


def read_tasks(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"INPUT ERROR | Missing {path}")
    tasks: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            task = parse_line(line)
            if task:
                task["index"] = len(tasks) + 1
                tasks.append(task)
        except Exception as exc:
            raise SystemExit(f"INPUT ERROR | links.txt line {line_number}: {exc}") from exc
    if not tasks:
        raise SystemExit("INPUT ERROR | No valid links found in links.txt")
    return tasks


def filename_from_headers(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    timeout = as_int(cfg("REQUEST_TIMEOUT", 60), 60, 10, 300)
    try:
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        cd = response.headers.get("content-disposition", "")
        for pattern in (r"filename\*=UTF-8''([^;]+)", r'filename="([^"]+)"', r"filename=([^;]+)"):
            match = re.search(pattern, cd, re.IGNORECASE)
            if match:
                filename = sanitize_filename(match.group(1).strip(" '\""))
                if len(filename) > 2:
                    return filename
    except Exception:
        pass

    parsed = urllib.parse.urlparse(url)
    filename = sanitize_filename(os.path.basename(parsed.path))
    if "." in filename and len(filename) > 2:
        return filename
    return f"download_{int(time.time())}.bin"


def with_heartbeat(label: str, func: Callable[[], Any]) -> Any:
    quiet = bool(cfg("QUIET_LOGS", True))
    if not quiet:
        return func()

    stop = threading.Event()
    started = time.time()

    def beat() -> None:
        while not stop.wait(90):
            elapsed = int(time.time() - started)
            Log.line(f"{label} | still running | {elapsed}s")

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        return func()
    finally:
        stop.set()


def download_with_aria2(url: str, output_path: Path) -> bool:
    if str(cfg("DOWNLOAD_ENGINE", "aria2")).lower() not in {"aria2", "auto"}:
        return False
    if not shutil.which("aria2c"):
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--quiet=true",
        "--show-console-readout=false",
        "--summary-interval=0",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--continue=true",
        "--check-certificate=false",
        "--file-allocation=none",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "-d", str(output_path.parent),
        "-o", output_path.name,
        url,
    ]

    def run() -> None:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    try:
        with_heartbeat(f"DOWNLOAD {output_path.name}", run)
        return output_path.exists() and output_path.stat().st_size > 0
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()
        Log.line(f"DOWNLOAD | aria2 fallback | {err[-500:] if err else exc}")
        return False
    except Exception as exc:
        Log.line(f"DOWNLOAD | aria2 fallback | {exc}")
        return False


def download_with_requests(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    timeout = as_int(cfg("REQUEST_TIMEOUT", 60), 60, 10, 300)
    chunk_size = as_int(cfg("CHUNK_SIZE_MB", 1), 1, 1, 16) * 1024 * 1024
    last_report = 0.0

    with requests.get(url, headers=headers, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0) or 0)
        downloaded = 0
        with output_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if not bool(cfg("QUIET_LOGS", True)) and now - last_report >= 10:
                    percent = f"{downloaded / total * 100:.1f}%" if total else "unknown"
                    Log.line(f"DOWNLOAD | {output_path.name} | {format_size(downloaded)} / {format_size(total)} | {percent}")
                    last_report = now


def download_file(url: str, output_path: Path) -> None:
    started = time.time()
    if not download_with_aria2(url, output_path):
        download_with_requests(url, output_path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"download failed or empty file: {output_path.name}")
    Log.line(f"DOWNLOAD | done | {output_path.name} | {format_size(output_path.stat().st_size)} | {time.time() - started:.1f}s")


def copy_stream(source: Any, member_name: str, extract_dir: Path) -> Path:
    destination = unique_path(extract_dir, member_name)
    with destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    return destination


def extract_archive(archive_path: Path, extract_dir: Path) -> List[Path]:
    archive_name = archive_path.name.lower()
    extracted: List[Path] = []
    extract_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            for member in zip_ref.infolist():
                if not member.is_dir() and not os.path.basename(member.filename).startswith("."):
                    with zip_ref.open(member) as source:
                        extracted.append(copy_stream(source, member.filename, extract_dir))
    elif archive_name.endswith(".rar"):
        if rarfile is None:
            raise RuntimeError("rarfile is not installed")
        with rarfile.RarFile(archive_path, "r") as rar_ref:
            for member in rar_ref.infolist():
                if not member.isdir() and not os.path.basename(member.filename).startswith("."):
                    with rar_ref.open(member) as source:
                        extracted.append(copy_stream(source, member.filename, extract_dir))
    elif archive_name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive_path, "r:*") as tar_ref:
            for member in tar_ref.getmembers():
                if member.isfile() and not os.path.basename(member.name).startswith("."):
                    source = tar_ref.extractfile(member)
                    if source:
                        extracted.append(copy_stream(source, member.name, extract_dir))
    elif archive_name.endswith(".7z"):
        if not shutil.which("7z"):
            raise RuntimeError("7z command is not installed")
        temp_dir = extract_dir / "sevenzip_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["7z", "x", "-y", f"-o{temp_dir}", str(archive_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for file_path in temp_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                destination = unique_path(extract_dir, file_path.name)
                shutil.move(str(file_path), destination)
                extracted.append(destination)
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        raise RuntimeError(f"unsupported archive type: {archive_path.name}")

    extracted.sort(key=lambda path: path.name.lower())
    Log.line(f"EXTRACT | done | {archive_path.name} | {len(extracted)} files | {time.time() - started:.1f}s")
    return extracted


class Uploader:
    def __init__(self) -> None:
        require_authorized()
        self.token = require_token()
        self.repo_id = env_or_cfg("REPO_ID", "REPO_ID", "DevDoCode/DDL2")
        self.path_in_repo = env_or_cfg("PATH_IN_REPO", "PATH_IN_REPO", "cdn/movies").strip("/")
        self.branch = env_or_cfg("BRANCH", "BRANCH", "movies") or "movies"
        self.repo_type = env_or_cfg("REPO_TYPE", "REPO_TYPE", "model") or "model"
        self.api = HfApi()

        Log.section("HF UPLOADER")
        Log.line(f"TARGET | repo={self.repo_id} | branch={self.branch} | path={self.path_in_repo or '[root]'} | type={self.repo_type}")
        login(token=self.token, add_to_git_credential=False)
        self.api.create_repo(repo_id=self.repo_id, repo_type=self.repo_type, token=self.token, exist_ok=True)
        self.ensure_branch()

    def ensure_branch(self) -> None:
        if self.branch == "main":
            return
        try:
            refs = self.api.list_repo_refs(repo_id=self.repo_id, repo_type=self.repo_type, token=self.token)
            existing = {branch.name for branch in refs.branches}
            if self.branch not in existing:
                self.api.create_branch(repo_id=self.repo_id, branch=self.branch, repo_type=self.repo_type, token=self.token)
                Log.line(f"BRANCH | created | {self.branch}")
        except Exception as exc:
            Log.line(f"BRANCH | fallback main | {exc}")
            self.branch = "main"

    def remote_path(self, file_path: Path) -> str:
        filename = sanitize_filename(file_path.name)
        return f"{self.path_in_repo}/{filename}" if self.path_in_repo else filename

    def upload_file(self, file_path: Path) -> Dict[str, Any]:
        remote = self.remote_path(file_path)
        file_size = file_path.stat().st_size
        started = time.time()
        Log.line(f"UPLOAD | start | {file_path.name} | {format_size(file_size)}")

        captured = io.StringIO()

        def do_upload() -> None:
            self.api.upload_file(
                path_or_fileobj=str(file_path),
                path_in_repo=remote,
                repo_id=self.repo_id,
                repo_type=self.repo_type,
                revision=self.branch,
                token=self.token,
                commit_message=f"Upload {file_path.name}",
            )

        try:
            if bool(cfg("QUIET_LOGS", True)):
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    with_heartbeat(f"UPLOAD {file_path.name}", do_upload)
            else:
                do_upload()
        except Exception as exc:
            hidden = captured.getvalue().strip()
            if hidden:
                Log.line(f"UPLOAD | details | {hidden[-1200:]}")
            raise exc

        url = f"https://huggingface.co/{self.repo_id}/blob/{self.branch}/{remote}"
        Log.line(f"UPLOAD | done | {file_path.name} | {time.time() - started:.1f}s")
        return {"ok": True, "file": file_path.name, "size": file_size, "url": url}

    def process(self, task: Dict[str, Any], total: int) -> List[Dict[str, Any]]:
        number = int(task["index"])
        work_dir = Path(tempfile.mkdtemp(prefix="hf_upload_"))
        try:
            filename = str(task.get("custom_filename") or filename_from_headers(str(task["url"])))
            Log.section(f"TASK {number}/{total}")
            Log.line(f"FILE | {filename}")
            download_path = unique_path(work_dir / "downloads", filename)
            download_file(str(task["url"]), download_path)

            if task.get("unzip") and download_path.name.lower().endswith(ARCHIVE_EXTENSIONS):
                files = extract_archive(download_path, work_dir / "extracted")
            else:
                if task.get("unzip"):
                    Log.line("EXTRACT | skipped | downloaded file is not an archive")
                files = [download_path]

            if not files:
                raise RuntimeError("no files found to upload")

            result = [self.upload_file(file_path) for file_path in files]
            Log.line(f"TASK {number}/{total} | done")
            return result
        except Exception as exc:
            Log.line(f"TASK {number}/{total} | failed | {exc}")
            return [{"ok": False, "file": str(task.get("custom_filename") or task.get("url")), "error": str(exc)}]
        finally:
            if bool(cfg("DELETE_LOCAL_AFTER_UPLOAD", True)):
                shutil.rmtree(work_dir, ignore_errors=True)


def write_summary(results: List[Dict[str, Any]]) -> None:
    success = [item for item in results if item.get("ok")]
    failed = [item for item in results if not item.get("ok")]
    lines = ["# Hugging Face Upload Summary", "", f"Successful: {len(success)}", f"Failed: {len(failed)}", ""]
    if success:
        lines.append("## Uploaded")
        for item in success:
            lines.append(f"- `{item['file']}` — {format_size(int(item['size']))} — {item['url']}")
        lines.append("")
    if failed:
        lines.append("## Failed")
        for item in failed:
            lines.append(f"- `{item.get('file', 'unknown')}` — {item.get('error', 'Unknown error')}")
        lines.append("")
    text = "\n".join(lines)
    Path("upload_summary.md").write_text(text + "\n", encoding="utf-8")
    Log.section("SUMMARY")
    Log.line(f"SUCCESS | {len(success)}")
    Log.line(f"FAILED  | {len(failed)}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main() -> int:
    links_file = Path(env_or_cfg("LINKS_FILE", "LINKS_FILE", "links.txt"))
    tasks = read_tasks(links_file)
    max_workers = as_int(os.environ.get("MAX_WORKERS") or cfg("MAX_WORKERS", 1), 1, 1, 10)

    uploader = Uploader()
    Log.line(f"QUEUE | tasks={len(tasks)} | concurrent_tasks={max_workers}")

    results: List[Dict[str, Any]] = []
    if max_workers == 1:
        for task in tasks:
            results.extend(uploader.process(task, len(tasks)))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(uploader.process, task, len(tasks)) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                results.extend(future.result())

    write_summary(results)
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
