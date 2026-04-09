"""
SaveSigner - MandarinJuice integration for save file signing.

Handles bruteforce and re-signing operations for RE Engine saves.
"""

import asyncio
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import py7zr

# MandarinJuice save signing profiles
SAVE_PROFILES = {
    "re9": {
        "name": "Resident Evil 9 Requiem",
        "profile": "Resident Evil 9 Requiem v1.bin",
    },
    "dd2": {
        "name": "Dragon's Dogma 2",
        "profile": "Dragon's Dogma 2 v1.bin",
    },
    "mhwilds": {
        "name": "Monster Hunter Wilds",
        "profile": "Monster Hunter Wilds v1.bin",
    },
    "kunitsu": {
        "name": "Kunitsu-Gami Path of the Goddess",
        "profile": "Kunitsu-Gami Path of the Goddess v1.bin",
    },
    "deadrising": {
        "name": "Dead Rising Deluxe Remaster",
        "profile": "Dead Rising Deluxe Remaster v1.bin",
    },
    "mhstories3": {
        "name": "Monster Hunter Stories 3 Twisted Reflection",
        "profile": "Monster Hunter Stories 3 Twisted Reflection v1.bin",
    },
    "megaman": {
        "name": "Mega Man Star Force Legacy Collection",
        "profile": "Mega Man Star Force Legacy Collection v1.bin",
    },
}


class SaveSigner:
    """Handles MandarinJuice CLI interactions for save signing."""

    def __init__(self, data_path: Path):
        """Initialize SaveSigner with cog data path."""
        self.data_path = data_path
        self.tools_path = data_path / "tools"
        self.cli_path = self.tools_path / "mandarin-juice-cli"
        self.profiles_path = self.tools_path / "profiles"

    def get_tool_path(self) -> Path | None:
        """Get path to MandarinJuice CLI. Returns None if not installed."""
        if self.cli_path.exists() and self.cli_path.is_file():
            return self.cli_path
        return None

    def get_profile_path(self, game: str) -> Path | None:
        """Get path to game profile file. Returns None if not found."""
        if game not in SAVE_PROFILES:
            return None

        profile_filename = SAVE_PROFILES[game]["profile"]
        profile_path = self.profiles_path / profile_filename

        if profile_path.exists():
            return profile_path
        return None

    def is_tool_installed(self) -> bool:
        """Check if MandarinJuice CLI is installed."""
        return self.get_tool_path() is not None

    def get_available_profiles(self) -> list[str]:
        """Get list of installed game profiles."""
        available = []
        for game_id in SAVE_PROFILES:
            if self.get_profile_path(game_id):
                available.append(game_id)
        return available

    async def run_bruteforce(
        self, game: str, save_archive: bytes, progress_callback=None
    ) -> dict | None:
        """
        Run bruteforce to find User ID.

        Args:
            game: Game profile ID (e.g., "re9")
            save_archive: Archive file contents (zip or 7z)
            progress_callback: Optional async function to call with stdout lines

        Returns:
            dict with "user_id" (str) and "time" (float), or None if failed
        """
        tool_path = self.get_tool_path()
        profile_path = self.get_profile_path(game)

        if not tool_path or not profile_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "input"
            extract_dir.mkdir()
            input_dir.mkdir()

            # Extract archive
            archive_path = tmpdir_path / "archive"
            archive_path.write_bytes(save_archive)

            try:
                # Try 7z first
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception:
                # Fall back to zip
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(extract_dir)
                except Exception:
                    return None

            # Find the first .bin file recursively (prefer files other than data000/data001)
            data_path = None
            fallback_path = None

            # Sort files so we predictably pick the lowest numbered one
            bin_files = sorted(extract_dir.rglob("*.bin"), key=lambda p: p.name.lower())

            for file_path in bin_files:
                name_lower = file_path.name.lower()

                # Blacklist data000, data001, and ANY file with "slot" in the name
                if (
                    name_lower == "data000.bin"
                    or name_lower == "data001.bin"
                    or "slot" in name_lower
                ):
                    if fallback_path is None:
                        fallback_path = file_path
                    continue

                data_path = file_path
                break

            if not data_path:
                data_path = fallback_path

            if not data_path:
                return None

            # Copy the first save file to input directory
            shutil.copy(data_path, input_dir / data_path.name)

            # Run bruteforce
            cmd = [
                str(tool_path),
                "-m",
                "b",
                "-g",
                str(profile_path),
                "-p",
                str(input_dir),
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

                # Read output in chunks to handle \r progress bars correctly
                if proc.stdout is None:
                    return None

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
                        buf = buf[idx + 1 :]

                        if line_str and progress_callback:
                            await progress_callback(line_str)

                        # Parse output for "Found UserID: XXXXX"
                        match = re.search(r"Found UserID:\s*(\d+)", line_str)
                        if match:
                            user_id = match.group(1)

                if buf:
                    line_str = buf.decode("utf-8", errors="ignore").strip()
                    if line_str and progress_callback:
                        await progress_callback(line_str)
                    match = re.search(r"Found UserID:\s*(\d+)", line_str)
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
        self, game: str, save_archive: bytes, old_id: str, new_id: str
    ) -> bytes | None:
        """
        Run re-sign operation.

        Args:
            game: Game profile ID (e.g., "re9")
            save_archive: Archive file contents (zip or 7z)
            old_id: Original User ID
            new_id: New User ID to sign to

        Returns:
            Zip file bytes containing re-signed saves, or None if failed
        """
        tool_path = self.get_tool_path()
        profile_path = self.get_profile_path(game)

        if not tool_path or not profile_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "input"
            extract_dir.mkdir()
            input_dir.mkdir()

            # Extract archive
            archive_path = tmpdir_path / "archive"
            archive_path.write_bytes(save_archive)

            try:
                # Try 7z first
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception:
                # Fall back to zip
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(extract_dir)
                except Exception:
                    return None

            # Copy all .bin save files to input directory
            for file_path in extract_dir.rglob("*.bin"):
                shutil.copy(file_path, input_dir / file_path.name)

            # Check if we have any save files
            save_files = list(input_dir.glob("*.bin"))
            if not save_files:
                return None

            # Run re-sign
            cmd = [
                str(tool_path),
                "-m",
                "r",
                "-g",
                str(profile_path),
                "-p",
                str(input_dir),
                "-uI",
                old_id,
                "-uO",
                new_id,
            ]

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                stdout, _ = await proc.communicate()
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            except Exception as e:
                return None
            finally:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()

            # The output goes to the current working directory's _OUTPUT folder
            # Since MandarinJuice puts it relative to its own binary, we need to locate it
            output_base = tool_path.parent / "_OUTPUT"
            if not output_base.exists():
                return None

            # Find the most recent resigned directory
            resigned_dirs = sorted(output_base.glob("*_resigned"), reverse=True)
            if not resigned_dirs:
                return None

            output_dir = resigned_dirs[0] / new_id
            if not output_dir.exists():
                return None

            # Collect all output files and create zip
            output_files = list(output_dir.glob("*.bin"))
            if not output_files:
                return None

            # Create zip in memory
            zip_buffer = Path(tmpdir_path / "output.zip")
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_files:
                    zipf.write(file_path, file_path.name)

            # Clean up _OUTPUT directory
            shutil.rmtree(output_base, ignore_errors=True)

            return zip_buffer.read_bytes()

        return None
