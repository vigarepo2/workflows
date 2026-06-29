#!/usr/bin/env python3
"""Authorized Hugging Face uploader for GitHub Actions.

Use only for files you own or have permission to store and redistribute.

links.txt line formats:
  https://example.com/file.ext
  https://example.com/file.ext -n new-name.ext
  https://example.com/archive.zip -unzip

Token stays outside code. Set HF_TOKEN or HF_WRITE_TOKEN as a GitHub secret.
Default non-token settings:
  REPO_ID=DevDoCode/DDL2
  PATH_IN_REPO=cdn/movies
  BRANCH=movies
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import requests
from huggingface_hub import HfApi, login
from tqdm.auto import tqdm

try:
    import rarfile
except Exception:
    rarfile = None

ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def env(name: str, default: str = "", required: bool = False) -> str:
    value = os.environ.get(name, default).strip()
    if required and not value:
        raise SystemExit(f"❌ Missing required environment variable: {name}")
    return value


def token_env() -> str:
    token = os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HF_WRITE_TOKEN", "").strip()
    if not token:
        raise SystemExit("❌ Missing Hugging Face token. Add GitHub secret HF_TOKEN or HF_WRITE_TOKEN.")
    return token


def format_size(size_bytes: int) -> str:
    value = float(size_bytes or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
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


def parse_line(line: str) -> Optional[Dict[str, object]]:
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
        raise ValueError(f"Invalid URL: {raw}")

    return {"raw": raw, "url": url, "custom_filename": custom_filename, "unzip": unzip}


def get_real_filename(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=20)
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


def download_with_aria2(url: str, output_path: Path) -> bool:
    if not shutil.which("aria2c"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c", "--allow-overwrite=true", "--auto-file-renaming=false",
        "--continue=true", "--check-certificate=false", "--max-connection-per-server=16",
        "--split=16", "--min-split-size=1M", "--summary-interval=10",
        "--console-log-level=warn", "-d", str(output_path.parent), "-o", output_path.name, url,
    ]
    try:
        subprocess.run(cmd, check=True)
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as exc:
        print(f"⚠️ aria2 failed, fallback to requests: {exc}")
        return False


def download_with_requests(url: str, output_path: Path) -> None:
    headers = {"User-Agent": USER_AGENT}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0) or 0)
        with open(output_path, "wb") as file_handle:
            with tqdm(total=total, unit="B", unit_scale=True, desc=output_path.name[:35]) as bar:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file_handle.write(chunk)
                        bar.update(len(chunk))


def download_file(url: str, output_path: Path) -> None:
    print(f"🚀 Downloading: {output_path.name}")
    if download_with_aria2(url, output_path):
        return
    download_with_requests(url, output_path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Download failed or file is empty: {output_path.name}")


def copy_stream(source, member_name: str, extract_dir: Path) -> Path:
    destination = unique_path(extract_dir, member_name)
    with open(destination, "wb") as target:
        shutil.copyfileobj(source, target)
    return destination


def extract_archive(archive_path: Path, extract_dir: Path) -> List[Path]:
    archive_name = archive_path.name.lower()
    extracted: List[Path] = []
    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"📦 Extracting: {archive_path.name}")

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
        subprocess.run(["7z", "x", "-y", f"-o{temp_dir}", str(archive_path)], check=True)
        for file_path in temp_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                destination = unique_path(extract_dir, file_path.name)
                shutil.move(str(file_path), destination)
                extracted.append(destination)
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        raise RuntimeError(f"Unsupported archive type: {archive_path.name}")

    extracted.sort(key=lambda path: path.name.lower())
    print(f"✅ Extracted {len(extracted)} file(s)")
    return extracted


class Uploader:
    def __init__(self) -> None:
        if env("CONFIRM_RIGHTS", "false").lower() != "true":
            raise SystemExit("❌ Set CONFIRM_RIGHTS=true only for files you own or have permission to upload.")

        self.token = token_env()
        self.repo_id = env("REPO_ID", "DevDoCode/DDL2")
        self.path_in_repo = env("PATH_IN_REPO", "cdn/movies").strip("/")
        self.branch = env("BRANCH", "movies") or "movies"
        self.repo_type = env("REPO_TYPE", "model") or "model"
        self.api = HfApi()

        print("🔐 Logging in to Hugging Face...")
        login(token=self.token, add_to_git_credential=False)
        self.api.create_repo(repo_id=self.repo_id, repo_type=self.repo_type, token=self.token, exist_ok=True)
        self.ensure_branch()
        print(f"✅ Target: {self.repo_id}/{self.path_in_repo or '[root]'}")
        print(f"🌿 Branch: {self.branch}")
        print("-" * 70)

    def ensure_branch(self) -> None:
        if self.branch == "main":
            return
        try:
            refs = self.api.list_repo_refs(repo_id=self.repo_id, repo_type=self.repo_type, token=self.token)
            existing = {branch.name for branch in refs.branches}
            if self.branch not in existing:
                print(f"🌿 Creating Hugging Face branch: {self.branch}")
                self.api.create_branch(repo_id=self.repo_id, branch=self.branch, repo_type=self.repo_type, token=self.token)
        except Exception as exc:
            print(f"⚠️ Branch check/create failed: {exc}")
            print("⚠️ Falling back to main")
            self.branch = "main"

    def remote_path(self, file_path: Path) -> str:
        filename = sanitize_filename(file_path.name)
        return f"{self.path_in_repo}/{filename}" if self.path_in_repo else filename

    def upload_file(self, file_path: Path) -> Dict[str, object]:
        remote = self.remote_path(file_path)
        file_size = file_path.stat().st_size
        print(f"☁️ Uploading: {file_path.name} ({format_size(file_size)}) → {remote}")
        self.api.upload_file(
            path_or_fileobj=str(file_path),
            path_in_repo=remote,
            repo_id=self.repo_id,
            repo_type=self.repo_type,
            revision=self.branch,
            token=self.token,
            commit_message=f"Upload {file_path.name}",
        )
        url = f"https://huggingface.co/{self.repo_id}/blob/{self.branch}/{remote}"
        print(f"✅ Uploaded: {url}")
        return {"ok": True, "file": file_path.name, "size": file_size, "url": url}

    def process(self, task: Dict[str, object]) -> List[Dict[str, object]]:
        print("\n" + "=" * 70)
        print(f"🎯 Processing: {task['raw']}")
        work_dir = Path(tempfile.mkdtemp(prefix="hf_upload_"))
        try:
            filename = str(task.get("custom_filename") or get_real_filename(str(task["url"])))
            download_path = unique_path(work_dir / "downloads", filename)
            download_file(str(task["url"]), download_path)

            if task.get("unzip") and download_path.name.lower().endswith(ARCHIVE_EXTENSIONS):
                files = extract_archive(download_path, work_dir / "extracted")
            else:
                if task.get("unzip"):
                    print("⚠️ -unzip used but downloaded file is not an archive. Uploading directly.")
                files = [download_path]

            if not files:
                raise RuntimeError("No files found to upload")
            return [self.upload_file(file_path) for file_path in files]
        except Exception as exc:
            print(f"❌ Failed: {exc}")
            return [{"ok": False, "file": str(task.get("custom_filename") or task.get("url")), "error": str(exc)}]
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


def read_tasks(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise SystemExit(f"❌ Missing links file: {path}")
    tasks: List[Dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            task = parse_line(line)
            if task:
                tasks.append(task)
        except Exception as exc:
            raise SystemExit(f"❌ Invalid links.txt line {line_no}: {exc}") from exc
    if not tasks:
        raise SystemExit("❌ No valid links found in links.txt")
    return tasks


def write_summary(results: List[Dict[str, object]]) -> None:
    success = [item for item in results if item.get("ok")]
    failed = [item for item in results if not item.get("ok")]
    lines = ["# Hugging Face Upload Summary", "", f"✅ Successful: **{len(success)}**", f"❌ Failed: **{len(failed)}**", ""]
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
    print("\n" + text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main() -> int:
    links_file = Path(env("LINKS_FILE", "links.txt"))
    tasks = read_tasks(links_file)
    uploader = Uploader()
    max_workers = max(1, min(int(env("MAX_WORKERS", "4") or "4"), 10))
    print(f"🔥 Starting {len(tasks)} task(s) with {max_workers} worker(s)")
    results: List[Dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(uploader.process, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.extend(future.result())
    write_summary(results)
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
