import atexit
import fcntl
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import undetected_chromedriver as uc

LOCK_FILE = Path("/tmp/uc_chrome_init.lock")
LOCK_TIMEOUT = 120

_temp_dirs: list[str] = []

def _cleanup_temp_dirs():
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)

atexit.register(_cleanup_temp_dirs)

def _get_chrome_major_version() -> int | None:
    """Detect the installed Chrome/Chromium major version number.
    UC's built-in detection only checks google-chrome, not chromium."""
    for binary in ["chromium", "google-chrome", "google-chrome-stable", "chromium-browser"]:
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Output looks like: "Chromium 147.0.7727.55 built on ..."
                version_str = result.stdout.strip().split()[1]
                major = int(version_str.split(".")[0])
                logging.info("Detected Chrome/Chromium version %d (from %s)", major, binary)
                return major
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            continue
    logging.warning("Could not detect Chrome version, letting UC guess")
    return None

def _ensure_driver_available(version_main: int | None) -> str:
    """Ensure chromedriver binary exists and matches the installed Chrome version.
    Always calls patcher.auto() so it self-heals when Chrome updates."""
    patcher = uc.Patcher(version_main=version_main)
    if not os.path.exists(patcher.executable_path):
        logging.info("ChromeDriver not found, downloading for Chrome %s...", version_main)
        patcher.auto()
    else:
        # Re-patch to ensure the binary matches the current Chrome version.
        # This is fast if the version already matches (UC skips the download).
        logging.info("Verifying ChromeDriver matches Chrome %s...", version_main)
        patcher.auto()
    return patcher.executable_path

def create_chrome_driver(
    *,
    user_agent: str | None = None,
    profile_dir: str | Path | None = None,
    page_load_timeout: int = 90,
    script_timeout: int = 60,
    extra_args: list[str] | None = None,
) -> uc.Chrome:
    """Create an undetected_chromedriver instance isolated from other concurrent
    instances. Each call copies the chromedriver binary to a unique temp
    directory so multiple scripts can run truly in parallel without the patcher
    or process manager interfering across instances."""

    if profile_dir:
        _clean_stale_profile_lock(Path(profile_dir))

    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-sync")
    chrome_options.add_argument("--disable-translate")
    chrome_options.add_argument("--metrics-recording-only")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--safebrowsing-disable-auto-update")
    chrome_options.add_argument("--force-device-scale-factor=1")

    if user_agent:
        chrome_options.add_argument(f"--user-agent={user_agent}")
        logging.info("Using custom User-Agent")

    if profile_dir:
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")

    for arg in (extra_args or []):
        chrome_options.add_argument(arg)

    chrome_options.page_load_strategy = "normal"

    lock_fd = open(LOCK_FILE, "w")
    try:
        logging.info("Acquiring Chrome init lock...")
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire Chrome init lock within {LOCK_TIMEOUT}s"
                    )
                time.sleep(1)

        logging.info("Lock acquired, initializing Chrome driver...")

        # Detect Chrome version so we can fetch the matching ChromeDriver.
        # UC's own detection only looks for google-chrome, not chromium.
        chrome_version = _get_chrome_major_version()

        # Ensure chromedriver exists at UC's default location and matches Chrome
        source_binary = _ensure_driver_available(chrome_version)

        # Copy to an isolated temp directory so this instance has its own binary
        tmp_dir = tempfile.mkdtemp(prefix="uc_driver_")
        _temp_dirs.append(tmp_dir)
        driver_path = os.path.join(tmp_dir, os.path.basename(source_binary))
        shutil.copy2(source_binary, driver_path)
        os.chmod(driver_path, 0o755)

        driver = uc.Chrome(
            options=chrome_options,
            version_main=chrome_version,
            driver_executable_path=driver_path,
        )
        driver.set_page_load_timeout(page_load_timeout)
        driver.set_script_timeout(script_timeout)
        logging.info("Chrome driver initialized successfully")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        logging.info("Chrome init lock released")

    return driver

def _clean_stale_profile_lock(profile_dir: Path):
    """Remove Chrome's singleton locks from a persistent profile directory.
    Leftover locks from crashed sessions prevent new instances from starting."""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_path = profile_dir / name
        if lock_path.exists() or lock_path.is_symlink():
            try:
                lock_path.unlink()
                logging.info(f"Removed stale {name}")
            except OSError:
                pass
