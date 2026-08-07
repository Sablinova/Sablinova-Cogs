"""
BL4Signer - Borderlands 4 save bruteforce + resign integration.

Wraps mi5hmash/Borderlands4SaveDataResigner (same author as MandarinJuice,
similar conventions). No profile file needed. Steam and EGS only.

    https://github.com/mi5hmash/Borderlands4SaveDataResigner

Modes (confirmed from -h / README):
    -m d  Decrypt   -p <dir>            -u <id>
    -m e  Encrypt   -p <dir>            -u <id>
    -m r  Re-sign   -p <dir>            -uI <old_id> -uO <new_id>
    -m b  Bruteforce -p <single .sav file>   (Steam only)

Examples:
    .\\bl4-savedata-resigner-cli -m b -p ".\\InputDirectory\\1.sav"
    .\\bl4-savedata-resigner-cli -m r -p ".\\InputDirectory" ^
        -uI 76561197960265729 -uO 76561197960265730

Resign/decrypt/encrypt output: README confirms modified files land in
"a newly created folder within Borderlands4SaveDataResigner/_OUTPUT/",
but doesn't document the subfolder's exact naming — run_resign scans
_OUTPUT/ broadly rather than assuming a specific pattern.

The bruteforce stdout format (how it reports the found User ID) isn't
documented either — _USER_ID_RE below is a placeholder guess, mirroring
MandarinJuice's "Found UserID: <digits>" wording. Run the tool once and
paste real output to confirm/fix this.
"""

import asyncio
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import py7zr
import rarfile

log = logging.getLogger("red.sablinova.pubhelper")

BL4_CLI_NAME = "bl4-savedata-resigner-cli"

# TODO: adjust this once you know the tool's real stdout format.
# This assumes something like "Found UserID: 12345" — same shape as
# MandarinJuice. If the tool prints differently, update this pattern.
_USER_ID_RE = re.compile(r"Found UserID:\s*(\d+)")


class BL4Signer:
    """Handles bruteforce save ID discovery and resigning for Borderlands 4."""

    def __init__(self, data_path: Path):
        """Initialize BL4Signer with cog data path."""
        self.data_path = data_path
        self.tools_path = data_path / "tools"
        self.cli_path = self.tools_path / BL4_CLI_NAME

    def get_tool_path(self) -> Path | None:
        """Get path to the BL4 bruteforce CLI. Returns None if not installed."""
        if self.cli_path.exists() and self.cli_path.is_file():
            return self.cli_path
        return None

    def is_tool_installed(self) -> bool:
        """Check if the BL4 bruteforce CLI is installed."""
        return self.get_tool_path() is not None

    @staticmethod
    def _extract_archive(save_archive: bytes, archive_path: Path, extract_dir: Path) -> None:
        """Extract a zip/7z/rar archive based on magic bytes. Raises ValueError on failure."""
        magic = save_archive[:10]
        log.debug("BL4Signer: archive magic bytes (first 10): %r", magic)

        archive_path.write_bytes(save_archive)

        if save_archive.startswith(b"Rar!\x1a\x07"):
            try:
                with rarfile.RarFile(archive_path) as archive:
                    archive.extractall(extract_dir)
            except Exception as exc:
                log.error("BL4Signer: RAR extraction failed! Magic: %r, error: %s", magic, exc)
                raise ValueError("Unsupported format")
        elif save_archive.startswith(b"7z\xbc\xaf\x27\x1c"):
            try:
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception as exc:
                log.error("BL4Signer: 7z extraction failed! Magic: %r, error: %s", magic, exc)
                raise ValueError("Unsupported format")
        elif save_archive.startswith(b"PK\x03\x04") or save_archive.startswith(b"PK\x05\x06"):
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception as exc:
                log.error("BL4Signer: ZIP extraction failed! Magic: %r, error: %s", magic, exc)
                raise ValueError("Unsupported format")
        else:
            log.error(
                "BL4Signer: unknown archive format! Magic: %r, length: %d bytes",
                magic, len(save_archive),
            )
            raise ValueError("Unsupported format")

    async def run_bruteforce(
        self,
        save_archive: bytes,
        progress_callback=None,
    ) -> dict | None:
        """
        Run bruteforce to find the User ID a BL4 save is signed to.
        Steam only — the tool's bruteforce mode doesn't support EGS IDs.

        Args:
            save_archive: Archive file contents (zip/7z/rar) containing a .sav
            progress_callback: Optional async function called with stdout lines

        Returns:
            dict with "user_id" (str), or None if not found / failed
        """
        tool_path = self.get_tool_path()
        if not tool_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "InputDirectory"
            extract_dir.mkdir()
            input_dir.mkdir()

            archive_path = tmpdir_path / "archive"
            try:
                self._extract_archive(save_archive, archive_path, extract_dir)
            except ValueError:
                return None

            # Find the .sav file to bruteforce. If there's more than one,
            # take the smallest.
            sav_files = sorted(extract_dir.rglob("*.sav"), key=lambda p: p.stat().st_size)

            if not sav_files:
                log.error("BL4Signer: no .sav files found in archive")
                return None

            src_save = sav_files[0]
            save_path = input_dir / src_save.name
            shutil.copy(src_save, save_path)

            # -p points directly at the .sav file for bruteforce mode
            # (unlike resign, which takes a directory). No -g profile
            # flag and no -u — bruteforce takes no other options per -h.
            # -q so the CLI doesn't block waiting for a keypress to exit
            # when run headlessly by the bot.
            cmd = [
                str(tool_path),
                "-m", "b",
                "-p", str(save_path),
                "-q",
            ]

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                user_id = None
                buf = b""

                if proc.stdout is None:
                    return None

                # Read in chunks to handle \r progress bars correctly
                while True:
                    chunk = await proc.stdout.read(1024)
                    if not chunk:
                        break
                    buf += chunk

                    while True:
                        n_idx = buf.find(b"\n")
                        r_idx = buf.find(b"\r")

                        if n_idx != -1 and r_idx != -1:
                            idx = min(n_idx, r_idx)
                        else:
                            idx = max(n_idx, r_idx)

                        if idx == -1:
                            break

                        line_str = buf[:idx].decode("utf-8", errors="ignore").strip()
                        buf = buf[idx + 1:]

                        if line_str and progress_callback:
                            await progress_callback(line_str)

                        match = _USER_ID_RE.search(line_str)
                        if match:
                            user_id = match.group(1)

                if buf:
                    line_str = buf.decode("utf-8", errors="ignore").strip()
                    if line_str and progress_callback:
                        await progress_callback(line_str)
                    match = _USER_ID_RE.search(line_str)
                    if match:
                        user_id = match.group(1)

                await proc.wait()

                if user_id:
                    return {"user_id": user_id}

            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            except Exception as e:
                if progress_callback:
                    await progress_callback(f"Exception running tool: {e}")
            finally:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()

        return None

    async def run_resign(
        self, save_archive: bytes, old_id: str, new_id: str
    ) -> bytes | None:
        """
        Run re-sign operation, moving a BL4 save from old_id to new_id.

        Args:
            save_archive: Archive file contents (zip/7z/rar) containing a .sav
            old_id: Original Steam ID the save is currently signed to
            new_id: Target Steam ID to sign the save to

        Returns:
            Zip file bytes containing the resigned save, or None if failed
        """
        tool_path = self.get_tool_path()
        if not tool_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "InputDirectory"
            extract_dir.mkdir()
            input_dir.mkdir()

            archive_path = tmpdir_path / "archive"
            try:
                self._extract_archive(save_archive, archive_path, extract_dir)
            except ValueError:
                return None

            # Copy all .sav files into InputDirectory — resign mode takes
            # a directory, not a single file (unlike bruteforce mode).
            for file_path in extract_dir.rglob("*.sav"):
                shutil.copy(file_path, input_dir / file_path.name)

            save_files = list(input_dir.glob("*.sav"))
            if not save_files:
                log.error("BL4Signer: no .sav files found in archive")
                return None

            cmd = [
                str(tool_path),
                "-m", "r",
                "-p", str(input_dir),
                "-uI", old_id,
                "-uO", new_id,
                "-q",
            ]

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                await proc.communicate()
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            except Exception as e:
                log.error("BL4Signer: resign subprocess failed: %s", e)
                return None
            finally:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()

            # Output location confirmed by README: "a newly created folder
            # within Borderlands4SaveDataResigner/_OUTPUT/". The exact
            # subfolder name isn't documented, so rather than guess a
            # pattern, take whichever subfolder was just created (most
            # recent mtime) — that's the one this run just produced.
            output_base = tool_path.parent / "_OUTPUT"
            if not output_base.exists():
                log.error(
                    "BL4Signer: expected output folder not found at %s",
                    output_base,
                )
                return None

            subfolders = [d for d in output_base.iterdir() if d.is_dir()]
            if not subfolders:
                log.error("BL4Signer: _OUTPUT exists but has no subfolders at %s", output_base)
                return None

            search_dir = max(subfolders, key=lambda d: d.stat().st_mtime)

            output_files = list(search_dir.rglob("*.sav"))
            if not output_files:
                log.error("BL4Signer: no resigned .sav files found under %s", search_dir)
                return None

            zip_buffer = tmpdir_path / "output.zip"
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_files:
                    zipf.write(file_path, file_path.name)

            shutil.rmtree(output_base, ignore_errors=True)

            return zip_buffer.read_bytes()
