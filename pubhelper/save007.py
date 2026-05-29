import asyncio
import inspect
import io
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from typing import Awaitable, Callable

import py7zr  # type: ignore[import-not-found]
import rarfile  # type: ignore[import-not-found]


ProgressCallback = Callable[[str], Awaitable[None]] | Callable[[str], None] | None


@dataclass
class Resign007Result:
    ok: bool
    zip_bytes: bytes | None
    zip_filename: str
    summary_json: dict | None
    stdout_tail: str
    error: str | None


class Save007Resigner:
    def __init__(self, log: logging.Logger):
        self.log = log

    @property
    def vendor_script(self) -> pathlib.Path:
        return (pathlib.Path(__file__).parent / "vendor" / "save_resign.py").resolve()

    def _zip_filename(self, new_id: str) -> str:
        safe_new_id = re.sub(r"[^A-Za-z0-9._-]", "_", new_id) or "unknown"
        return f"007_resigned_{safe_new_id}.zip"

    async def run_resign(
        self,
        archive_bytes: bytes,
        new_id: str,
        rewrite_0x00: bool,
        progress_callback: ProgressCallback,
        timeout_seconds: int = 600,
    ) -> Resign007Result:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = pathlib.Path(tmpdir)
            archive_path = tmpdir_path / "archive"
            extract_dir = tmpdir_path / "extracted"
            dst_dir = tmpdir_path / "resigned"
            archive_path.write_bytes(archive_bytes)
            extract_dir.mkdir()
            dst_dir.mkdir()

            extracted = self._extract_archive(archive_bytes, archive_path, extract_dir)
            if extracted:
                return extracted

            src_dir = self._find_save_root(extract_dir)
            if src_dir is None:
                return Resign007Result(
                    ok=False,
                    zip_bytes=None,
                    zip_filename=self._zip_filename(new_id),
                    summary_json=None,
                    stdout_tail="",
                    error="No index.save/data.save files found in archive",
                )

            cmd = [
                sys.executable,
                str(self.vendor_script),
                "--json",
                "resign",
                "--src",
                str(src_dir),
                "--dst",
                str(dst_dir),
                "--new-id",
                new_id,
                "--yes",
            ]
            if rewrite_0x00:
                cmd.append("--rewrite-0x00")
            return await self._run_process(cmd, new_id, progress_callback, timeout_seconds, dst_dir)

    def _extract_archive(
        self, archive_bytes: bytes, archive_path: pathlib.Path, extract_dir: pathlib.Path
    ) -> Resign007Result | None:
        magic = archive_bytes[:10]
        self.log.debug("save007: archive magic bytes (first 10): %r", magic)
        try:
            if archive_bytes.startswith(b"Rar!\x1a\x07"):
                with rarfile.RarFile(archive_path) as archive:
                    self._safe_extract_rar(archive, extract_dir)
                return None
            if archive_bytes.startswith(b"7z\xbc\xaf\x27\x1c"):
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    self._safe_extract_7z(archive, extract_dir)
                return None
            if archive_bytes.startswith(b"PK\x03\x04") or archive_bytes.startswith(b"PK\x05\x06"):
                with zipfile.ZipFile(archive_path, "r") as archive:
                    self._safe_extract_zip(archive, extract_dir)
                return None
        except Exception as exc:
            self.log.error("save007: archive extraction failed: %s", exc, exc_info=True)
            return Resign007Result(False, None, "", None, "", f"Failed to extract archive: {exc}")
        self.log.error("save007: unknown archive format; magic=%r len=%d", magic, len(archive_bytes))
        return Resign007Result(False, None, "", None, "", "Unsupported format")

    def _safe_extract_zip(self, archive: zipfile.ZipFile, extract_dir: pathlib.Path) -> None:
        for member in archive.infolist():
            self._validate_extract_path(extract_dir, member.filename)
        archive.extractall(extract_dir)

    def _safe_extract_rar(self, archive: rarfile.RarFile, extract_dir: pathlib.Path) -> None:
        for member in archive.infolist():
            self._validate_extract_path(extract_dir, member.filename)
        archive.extractall(extract_dir)

    def _safe_extract_7z(self, archive: py7zr.SevenZipFile, extract_dir: pathlib.Path) -> None:
        for name in archive.getnames():
            self._validate_extract_path(extract_dir, name)
        archive.extractall(extract_dir)

    def _validate_extract_path(self, extract_dir: pathlib.Path, member_name: str) -> None:
        base = extract_dir.resolve()
        target = (extract_dir / member_name).resolve()
        if os.path.commonpath([str(base), str(target)]) != str(base):
            raise ValueError(f"Archive member escapes extraction root: {member_name}")

    def _find_save_root(self, extract_dir: pathlib.Path) -> pathlib.Path | None:
        current = extract_dir
        while True:
            if self._tree_contains_save_files(current):
                entries = [p for p in current.iterdir()]
                if len(entries) == 1 and entries[0].is_dir() and not self._direct_contains_save_files(current):
                    nested = entries[0]
                    if self._tree_contains_save_files(nested):
                        current = nested
                        continue
                return current
            entries = [p for p in current.iterdir()]
            if len(entries) != 1 or not entries[0].is_dir():
                return None
            current = entries[0]

    def _tree_contains_save_files(self, root: pathlib.Path) -> bool:
        return any(path.name in {"index.save", "data.save"} for path in root.rglob("*"))

    def _direct_contains_save_files(self, root: pathlib.Path) -> bool:
        return any(path.name in {"index.save", "data.save"} for path in root.iterdir() if path.is_file())

    async def _run_process(
        self,
        cmd: list[str],
        new_id: str,
        progress_callback: ProgressCallback,
        timeout_seconds: int,
        dst_dir: pathlib.Path,
    ) -> Resign007Result:
        stdout_tail = ""
        last_non_empty = ""
        zip_filename = self._zip_filename(new_id)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            if proc.stdout is None:
                return Resign007Result(
                    False, None, zip_filename, None, "", "Failed to capture process output"
                )
            waiter = asyncio.create_task(proc.wait())
            reader = asyncio.create_task(self._read_stdout(proc.stdout, progress_callback))
            started = time.monotonic()
            try:
                stdout_tail, last_non_empty = await asyncio.wait_for(
                    reader, timeout=timeout_seconds
                )
                elapsed = time.monotonic() - started
                remaining = max(1.0, timeout_seconds - elapsed)
                await asyncio.wait_for(waiter, timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                reader.cancel()
                return Resign007Result(
                    False,
                    None,
                    zip_filename,
                    None,
                    stdout_tail,
                    f"Resign timed out after {timeout_seconds}s",
                )
        except Exception as exc:
            self.log.error("save007: process failed: %s", exc, exc_info=True)
            return Resign007Result(False, None, zip_filename, None, stdout_tail, str(exc))
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

        returncode = proc.returncode if proc else None
        if returncode != 0:
            return Resign007Result(
                False,
                None,
                zip_filename,
                self._parse_summary(last_non_empty),
                stdout_tail,
                f"Resigner exited with code {returncode}",
            )

        zip_bytes = await asyncio.to_thread(self._zip_directory, dst_dir)
        return Resign007Result(
            True,
            zip_bytes,
            zip_filename,
            self._parse_summary(last_non_empty),
            stdout_tail,
            None,
        )

    async def _read_stdout(
        self, stdout: asyncio.StreamReader, progress_callback: ProgressCallback
    ) -> tuple[str, str]:
        buffer = b""
        tail = ""
        last_non_empty = ""
        while True:
            chunk = await stdout.read(1024)
            if not chunk:
                break
            buffer += chunk
            buffer, tail, last_non_empty = await self._consume_buffer(
                buffer, tail, last_non_empty, progress_callback
            )
        if buffer:
            line = buffer.decode("utf-8", errors="ignore").strip()
            tail = self._append_tail(tail, line)
            if line:
                last_non_empty = line
                await self._emit_progress(progress_callback, line)
        return tail, last_non_empty

    async def _consume_buffer(
        self,
        buffer: bytes,
        tail: str,
        last_non_empty: str,
        progress_callback: ProgressCallback,
    ) -> tuple[bytes, str, str]:
        while True:
            newline = buffer.find(b"\n")
            carriage = buffer.find(b"\r")
            indexes = [idx for idx in (newline, carriage) if idx != -1]
            if not indexes:
                return buffer, tail, last_non_empty
            idx = min(indexes)
            line = buffer[:idx].decode("utf-8", errors="ignore").strip()
            buffer = buffer[idx + 1 :]
            tail = self._append_tail(tail, line)
            if line:
                last_non_empty = line
                await self._emit_progress(progress_callback, line)

    async def _emit_progress(self, progress_callback: ProgressCallback, line: str) -> None:
        if not progress_callback:
            return
        result = progress_callback(line)
        if inspect.isawaitable(result):
            await result

    def _append_tail(self, tail: str, line: str) -> str:
        updated = f"{tail}\n{line}" if tail else line
        return updated[-4000:]

    def _parse_summary(self, line: str) -> dict | None:
        if not line:
            return None
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _zip_directory(self, root: pathlib.Path) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        return buffer.getvalue()
