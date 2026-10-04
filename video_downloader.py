#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
import hashlib
import requests
import time

from config import DOWNLOAD_DIR


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


class DownloadCancelled(Exception):
    """Raised when a user stops an active transfer."""


def _cancelled(cancel_check):
    return bool(cancel_check and cancel_check())


def _wait_before_retry(seconds, cancel_check):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _cancelled(cancel_check):
            raise DownloadCancelled
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def get_file_hash(filepath):
    """Compute SHA256 hash of a file for deduplication."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def download_video(url, output_path, progress_callback=None, cancel_check=None):
    """Download a video file from the given URL with streaming.
    Returns the path to the downloaded file, or None on failure."""
    s = requests.Session()
    s.headers.update(HEADERS)
    
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        try:
            if _cancelled(cancel_check):
                raise DownloadCancelled
            r = s.get(url, stream=True, timeout=60)
            
            if r.status_code == 429:
                retry_count += 1
                _wait_before_retry(5 * retry_count, cancel_check)
                continue
            
            if r.status_code != 200:
                return None
            
            total_size = int(r.headers.get("content-length", 0))
            
            # Check content type
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                cleanup_file(output_path)
                r.close()
                return None
            
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if _cancelled(cancel_check):
                        raise DownloadCancelled
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            percent = (downloaded / total_size) * 100
                            progress_callback(percent, downloaded, total_size)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                downloaded = os.path.getsize(output_path)
                if progress_callback:
                    progress_callback(100, downloaded, downloaded)
                return output_path
            
        except DownloadCancelled:
            cleanup_file(output_path)
            raise
        except requests.exceptions.RequestException:
            retry_count += 1
            _wait_before_retry(2, cancel_check)
        except Exception:
            retry_count += 1
            _wait_before_retry(2, cancel_check)
    
    return None


def download_with_redirects(url, output_path, progress_callback=None, cancel_check=None):
    """Download a video file, following redirects. Handles potential redirect chains."""
    s = requests.Session()
    s.headers.update(HEADERS)
    
    try:
        if _cancelled(cancel_check):
            raise DownloadCancelled
        r = s.get(url, stream=True, timeout=60, allow_redirects=True)
    except Exception:
        return None
    
    if r.status_code != 200:
        return None
    
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    
    try:
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if _cancelled(cancel_check):
                    raise DownloadCancelled
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        percent = (downloaded / total_size) * 100
                        progress_callback(percent, downloaded, total_size)
    except DownloadCancelled:
        cleanup_file(output_path)
        raise
    except Exception:
        cleanup_file(output_path)
        return None
    
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        downloaded = os.path.getsize(output_path)
        if progress_callback:
            progress_callback(100, downloaded, downloaded)
        return output_path
    
    return None


def cleanup_file(filepath):
    """Remove a file if it exists."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass


def get_video_info(url):
    """Get video file size and other info from URL headers."""
    s = requests.Session()
    s.headers.update(HEADERS)
    
    try:
        r = s.head(url, timeout=15, allow_redirects=True)
        size = int(r.headers.get("content-length", 0))
        content_type = r.headers.get("content-type", "")
        return {"size": size, "content_type": content_type, "status": r.status_code}
    except Exception:
        return {"size": 0, "content_type": "", "status": 0}
