#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import io
import logging
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
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from huggingface_hub import CommitOperationAdd, HfApi

try:
    import rarfile
except ImportError:
    rarfile = None

LINKS_FILE = Path("links.txt")
CHUNK_SIZE = 2 * 1024 * 1024
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 180
MAX_RETRIES = 3
MAX_DOWNLOAD_WORKERS = 8
ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="huggingface_hub")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def bounded_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def format_size(size: int | float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(max(size, 0))
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def progress_bar(percent: float, width: int = 18) -> str:
    percent = max(0.0, min(percent, 100.0))
    filled = int(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


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
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def filename_from_response(response: requests.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    patterns = (
        r"filename\*=UTF-8''([^;]+)",
        r'filename="([^"]+)"',
        r"filename=([^;]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, disposition, re.IGNORECASE)
        if match:
            filename = sanitize_filename(match.group(1).strip(" '\""))
            if len(filename) > 2:
                return filename

    filename = sanitize_filename(Path(urllib.parse.urlparse(response.url).path).name)
    return filename if "." in filename else f"download_{int(time.time())}.bin"


class LiveLine:
    def __init__(self, interval: float = 0.20) -> None:
        self.interval = interval
        self.last_render = 0.0
        self.lock = threading.Lock()
        self.open = False

    def update(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        with self.lock:
            if not force and now - self.last_render < self.interval:
                return
            sys.stdout.write(f"\r\033[2K{text}")
            sys.stdout.flush()
            self.last_render = now
            self.open = True

    def finish(self, text: str) -> None:
        with self.lock:
            sys.stdout.write(f"\r\033[2K{text}\n")
            sys.stdout.flush()
            self.last_render = time.monotonic()
            self.open = False

    def message(self, text: str) -> None:
        with self.lock:
            if self.open:
                sys.stdout.write("\n")
                self.open = False
            print(text, flush=True)


@dataclass(frozen=True)
class Task:
    index: int
    url: str
    custom_filename: str | None
    unzip: bool


@dataclass
class DownloadedTask:
    task: Task
    work_dir: Path
    file_path: Path | None = None
    error: str | None = None


@dataclass
class Result:
    ok: bool
    file: str
    size: int = 0
    url: str = ""
    error: str = ""


def parse_line(line: str, index: int) -> Task | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    unzip = raw.lower().endswith(" -unzip")
    if unzip:
        raw = raw[: -len(" -unzip")].strip()

    custom_filename = None
    if " -n " in raw:
        url, custom_filename = raw.split(" -n ", 1)
        custom_filename = sanitize_filename(custom_filename.strip())
    else:
        url = raw

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    return Task(index=index, url=url, custom_filename=custom_filename, unzip=unzip)


def read_tasks() -> list[Task]:
    if not LINKS_FILE.is_file():
        raise SystemExit("INPUT ERROR | links.txt is missing")

    tasks: list[Task] = []
    for line_number, line in enumerate(LINKS_FILE.read_text(encoding="utf-8").splitlines(), 1):
        try:
            task = parse_line(line, len(tasks) + 1)
        except ValueError as exc:
            raise SystemExit(f"INPUT ERROR | links.txt line {line_number}: {exc}") from exc
        if task:
            tasks.append(task)

    if not tasks:
        raise SystemExit("INPUT ERROR | links.txt contains no valid URLs")
    return tasks


class DownloadProgress:
    def __init__(self, total_files: int, line: LiveLine) -> None:
        self.total_files = total_files
        self.line = line
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.states: dict[int, dict[str, Any]] = {}

    def update(self, task: Task, filename: str, downloaded: int, total: int, status: str = "active") -> None:
        with self.lock:
            self.states[task.index] = {
                "filename": filename,
                "downloaded": downloaded,
                "total": total,
                "status": status,
            }
            self._render()

    def _render(self, force: bool = False) -> None:
        downloaded = sum(int(item["downloaded"]) for item in self.states.values())
        known_total = sum(int(item["total"]) for item in self.states.values() if int(item["total"]) > 0)
        unknown = (self.total_files - len(self.states)) + sum(
            1 for item in self.states.values() if int(item["total"]) <= 0
        )
        complete = sum(1 for item in self.states.values() if item["status"] == "done")
        failed = sum(1 for item in self.states.values() if item["status"] == "failed")
        elapsed = max(time.monotonic() - self.started, 0.001)
        speed = downloaded / elapsed

        if known_total and unknown == 0:
            percent = min(downloaded / known_total * 100, 100.0)
            remaining = max(known_total - downloaded, 0)
            eta = format_duration(remaining / speed) if speed > 0 else "--:--"
            text = (
                f"DOWNLOAD  {progress_bar(percent)} {percent:5.1f}% | "
                f"{complete}/{self.total_files} files | {format_size(downloaded)}/{format_size(known_total)} | "
                f"{format_size(speed)}/s | ETA {eta}"
            )
        else:
            text = (
                f"DOWNLOAD  {complete}/{self.total_files} files | {format_size(downloaded)} | "
                f"{format_size(speed)}/s"
            )
        if failed:
            text += f" | {failed} failed"
        self.line.update(text, force=force)

    def finish(self) -> None:
        with self.lock:
            downloaded = sum(int(item["downloaded"]) for item in self.states.values())
            complete = sum(1 for item in self.states.values() if item["status"] == "done")
            failed = sum(1 for item in self.states.values() if item["status"] == "failed")
            state = "COMPLETE" if failed == 0 else "PARTIAL"
            text = (
                f"DOWNLOAD  {state} | {complete}/{self.total_files} files | "
                f"{format_size(downloaded)} | {format_duration(time.monotonic() - self.started)}"
            )
            if failed:
                text += f" | {failed} failed"
            self.line.finish(text)


class UploadProgress:
    def __init__(self, file_path: Path, line: LiveLine) -> None:
        self.file_path = file_path
        self.total = file_path.stat().st_size
        self.line = line
        self.started = time.monotonic()
        self.maximum = 0
        self.lock = threading.Lock()
        self.line.update(
            f"UPLOAD    PREPARING | {file_path.name} | {format_size(self.total)}",
            force=True,
        )

    def update(self, position: int) -> None:
        with self.lock:
            self.maximum = max(self.maximum, min(position, self.total))
            elapsed = max(time.monotonic() - self.started, 0.001)
            speed = self.maximum / elapsed
            percent = self.maximum / self.total * 100 if self.total else 100.0
            remaining = max(self.total - self.maximum, 0)
            eta = format_duration(remaining / speed) if speed > 0 else "--:--"
            self.line.update(
                f"UPLOAD    {progress_bar(percent)} {percent:5.1f}% | {self.file_path.name} | "
                f"{format_size(self.maximum)}/{format_size(self.total)} | {format_size(speed)}/s | ETA {eta}"
            )

    def finish(self) -> None:
        self.line.finish(
            f"UPLOAD    COMPLETE | {self.file_path.name} | {format_size(self.total)} | "
            f"{format_duration(time.monotonic() - self.started)}"
        )

    def fail(self, error: str) -> None:
        self.line.finish(f"UPLOAD    FAILED | {self.file_path.name} | {error}")


class ProgressReader(io.BufferedReader):
    def __init__(self, path: Path, callback: Callable[[int], None]) -> None:
        raw = open(path, "rb", buffering=0)
        super().__init__(raw, buffer_size=CHUNK_SIZE)
        self.callback = callback
        self.tracking = False

    def enable_tracking(self) -> None:
        self.tracking = True

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        if self.tracking and data:
            self.callback(self.tell())
        return data


def download_task(task: Task, progress: DownloadProgress) -> DownloadedTask:
    work_dir = Path(tempfile.mkdtemp(prefix=f"hf_task_{task.index}_"))
    output_path: Path | None = None
    last_error = "Unknown download error"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            existing = output_path.stat().st_size if output_path and output_path.exists() else 0
            headers = {"User-Agent": USER_AGENT}
            if existing:
                headers["Range"] = f"bytes={existing}-"

            with requests.get(
                task.url,
                headers=headers,
                stream=True,
                allow_redirects=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            ) as response:
                if response.status_code == 416 and output_path and output_path.exists():
                    progress.update(task, output_path.name, existing, existing, "done")
                    return DownloadedTask(task=task, work_dir=work_dir, file_path=output_path)

                response.raise_for_status()

                if output_path is None:
                    filename = task.custom_filename or filename_from_response(response)
                    output_path = unique_path(work_dir / "downloads", filename)
                    existing = 0

                resumed = existing > 0 and response.status_code == 206
                if not resumed:
                    existing = 0

                content_length = int(response.headers.get("content-length", "0") or 0)
                total = existing + content_length if content_length else 0
                downloaded = existing
                mode = "ab" if resumed else "wb"
                progress.update(task, output_path.name, downloaded, total)

                with output_path.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        downloaded += len(chunk)
                        progress.update(task, output_path.name, downloaded, total)

                if output_path.stat().st_size == 0:
                    raise RuntimeError("server returned an empty file")
                if total and output_path.stat().st_size < total:
                    raise RuntimeError("connection closed before the download completed")

                final_size = output_path.stat().st_size
                progress.update(task, output_path.name, final_size, total or final_size, "done")
                return DownloadedTask(task=task, work_dir=work_dir, file_path=output_path)

        except Exception as exc:
            last_error = str(exc).strip() or exc.__class__.__name__
            if attempt < MAX_RETRIES:
                time.sleep(min(2 ** (attempt - 1), 4))

    filename = output_path.name if output_path else task.custom_filename or f"task-{task.index}"
    current_size = output_path.stat().st_size if output_path and output_path.exists() else 0
    progress.update(task, filename, current_size, current_size, "failed")
    return DownloadedTask(task=task, work_dir=work_dir, file_path=output_path, error=last_error)


def copy_member(source: Any, member_name: str, destination: Path) -> Path:
    output = unique_path(destination, Path(member_name).name)
    with output.open("wb") as target:
        shutil.copyfileobj(source, target)
    return output


def extract_archive(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    extracted: list[Path] = []

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not member.is_dir() and not Path(member.filename).name.startswith("."):
                    with package.open(member) as source:
                        extracted.append(copy_member(source, member.filename, destination))

    elif name.endswith(".rar"):
        if rarfile is None:
            raise RuntimeError("RAR support is unavailable")
        with rarfile.RarFile(archive) as package:
            for member in package.infolist():
                if not member.isdir() and not Path(member.filename).name.startswith("."):
                    with package.open(member) as source:
                        extracted.append(copy_member(source, member.filename, destination))

    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive, "r:*") as package:
            for member in package.getmembers():
                if member.isfile() and not Path(member.name).name.startswith("."):
                    source = package.extractfile(member)
                    if source:
                        with source:
                            extracted.append(copy_member(source, member.name, destination))

    elif name.endswith(".7z"):
        if not shutil.which("7z"):
            raise RuntimeError("7z is not installed")
        subprocess.run(
            ["7z", "e", "-y", f"-o{destination}", str(archive)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        extracted = [path for path in destination.iterdir() if path.is_file() and not path.name.startswith(".")]

    else:
        raise RuntimeError(f"unsupported archive type: {archive.name}")

    return sorted(extracted, key=lambda path: path.name.lower())


def chunks(items: list[Task], size: int) -> Iterable[list[Task]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class HubUploader:
    def __init__(self, line: LiveLine) -> None:
        self.token = env("HF_TOKEN")
        self.repo_id = env("HF_REPO_ID")
        self.path_in_repo = env("HF_PATH_IN_REPO").strip("/")
        self.branch = env("HF_BRANCH", "main") or "main"
        self.repo_type = env("HF_REPO_TYPE", "model").lower() or "model"
        self.line = line

        if not self.token:
            raise SystemExit("CONFIG ERROR | HF_TOKEN secret is missing")
        if not self.repo_id or "/" not in self.repo_id:
            raise SystemExit("CONFIG ERROR | Repository ID must use owner/repository format")
        if self.repo_type not in {"model", "dataset"}:
            raise SystemExit("CONFIG ERROR | Repository type must be model or dataset")

        self.api = HfApi(token=self.token)
        self.api.create_repo(
            repo_id=self.repo_id,
            repo_type=self.repo_type,
            token=self.token,
            exist_ok=True,
        )
        self.ensure_branch()

    def ensure_branch(self) -> None:
        if self.branch == "main":
            return
        refs = self.api.list_repo_refs(
            repo_id=self.repo_id,
            repo_type=self.repo_type,
            token=self.token,
        )
        if self.branch not in {item.name for item in refs.branches}:
            self.api.create_branch(
                repo_id=self.repo_id,
                repo_type=self.repo_type,
                branch=self.branch,
                token=self.token,
                exist_ok=True,
            )

    def remote_path(self, file_path: Path) -> str:
        filename = sanitize_filename(file_path.name)
        return f"{self.path_in_repo}/{filename}" if self.path_in_repo else filename

    def public_url(self, remote_path: str) -> str:
        prefix = "datasets/" if self.repo_type == "dataset" else ""
        revision = urllib.parse.quote(self.branch, safe="")
        path = urllib.parse.quote(remote_path, safe="/")
        return f"https://huggingface.co/{prefix}{self.repo_id}/blob/{revision}/{path}"

    def upload(self, file_path: Path) -> Result:
        remote_path = self.remote_path(file_path)
        progress = UploadProgress(file_path, self.line)

        try:
            with ProgressReader(file_path, progress.update) as reader:
                operation = CommitOperationAdd(
                    path_in_repo=remote_path,
                    path_or_fileobj=reader,
                )
                reader.enable_tracking()
                self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type=self.repo_type,
                    revision=self.branch,
                    operations=[operation],
                    commit_message=f"Upload {file_path.name}",
                    token=self.token,
                )

            progress.update(file_path.stat().st_size)
            progress.finish()
            url = self.public_url(remote_path)
            self.line.message(f"SUCCESS   {url}")
            return Result(ok=True, file=file_path.name, size=file_path.stat().st_size, url=url)

        except Exception as exc:
            error = str(exc).strip() or exc.__class__.__name__
            progress.fail(error)
            return Result(ok=False, file=file_path.name, error=error)


def write_summary(results: list[Result], uploader: HubUploader) -> None:
    successful = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    lines = [
        "# Hugging Face Upload Summary",
        "",
        f"- Repository: `{uploader.repo_id}`",
        f"- Type: `{uploader.repo_type}`",
        f"- Branch: `{uploader.branch}`",
        f"- Destination: `{uploader.path_in_repo or '/'}`",
        f"- Successful: **{len(successful)}**",
        f"- Failed: **{len(failed)}**",
        "",
    ]

    if successful:
        lines.extend(["## Uploaded", ""])
        lines.extend(f"- [{item.file}]({item.url}) — {format_size(item.size)}" for item in successful)
        lines.append("")

    if failed:
        lines.extend(["## Failed", ""])
        lines.extend(f"- `{item.file}` — {item.error}" for item in failed)
        lines.append("")

    summary = "\n".join(lines)
    Path("upload_summary.md").write_text(summary + "\n", encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")


def main() -> int:
    tasks = read_tasks()
    workers = bounded_int(env("DOWNLOAD_WORKERS", "3"), 3, 1, MAX_DOWNLOAD_WORKERS)
    line = LiveLine()
    uploader = HubUploader(line)
    results: list[Result] = []

    for batch in chunks(tasks, workers):
        download_progress = DownloadProgress(len(batch), line)
        downloaded: list[DownloadedTask] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [executor.submit(download_task, task, download_progress) for task in batch]
            for future in concurrent.futures.as_completed(futures):
                downloaded.append(future.result())

        download_progress.finish()
        downloaded.sort(key=lambda item: item.task.index)

        for item in downloaded:
            try:
                if item.error or not item.file_path:
                    error = item.error or "download failed"
                    line.message(f"FAILED    {item.task.custom_filename or item.task.url} | {error}")
                    results.append(Result(ok=False, file=item.task.custom_filename or item.task.url, error=error))
                    continue

                if item.task.unzip:
                    if not item.file_path.name.lower().endswith(ARCHIVE_EXTENSIONS):
                        raise RuntimeError("-unzip was requested for a non-archive file")
                    files = extract_archive(item.file_path, item.work_dir / "extracted")
                    if not files:
                        raise RuntimeError("archive contains no uploadable files")
                else:
                    files = [item.file_path]

                for file_path in files:
                    results.append(uploader.upload(file_path))

            except Exception as exc:
                error = str(exc).strip() or exc.__class__.__name__
                file_name = item.file_path.name if item.file_path else item.task.url
                line.message(f"FAILED    {file_name} | {error}")
                results.append(Result(ok=False, file=file_name, error=error))
            finally:
                shutil.rmtree(item.work_dir, ignore_errors=True)

    write_summary(results, uploader)
    return 0 if results and all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
