import asyncio
import contextlib
import inspect
import io
import json
import logging
import os
import pathlib
import re
import shlex
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

    def _log_step(self, message: str, **fields: object) -> None:
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"[savesign007] {message}"
        if suffix:
            line = f"{line} {suffix}"
        self.log.info(line)

    async def _report_step(self, progress_callback: ProgressCallback, line: str) -> None:
        await self._emit_progress(progress_callback, line)

    async def run_resign(
        self,
        archive_bytes: bytes,
        new_id: str,
        rewrite_0x00: bool,
        progress_callback: ProgressCallback,
        timeout_seconds: int = 600,
    ) -> Resign007Result:
        zip_filename = self._zip_filename(new_id)
        run_started = time.monotonic()
        self._log_step(
            "run start:",
            new_id=new_id,
            archive_bytes=len(archive_bytes),
            elapsed="0.000s",
        )
        await self._report_step(progress_callback, "Starting 007 resign workflow...")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = pathlib.Path(tmpdir)
            archive_path = tmpdir_path / "archive"
            extract_dir = tmpdir_path / "extracted"
            dst_dir = tmpdir_path / "resigned"
            try:
                write_started = time.monotonic()
                self._log_step("write archive start:", path=archive_path, size_bytes=len(archive_bytes))
                await self._report_step(progress_callback, "Writing archive to temporary storage...")
                await asyncio.to_thread(archive_path.write_bytes, archive_bytes)
                extract_dir.mkdir()
                dst_dir.mkdir()
                self._log_step(
                    "write archive finish:",
                    path=archive_path,
                    size_bytes=len(archive_bytes),
                    elapsed=f"{time.monotonic() - write_started:.3f}s",
                )
                await self._report_step(progress_callback, "Archive written; detecting archive type...")

                extract_started = time.monotonic()
                archive_type = self._detect_archive_type(archive_bytes)
                self._log_step(
                    "archive detect:",
                    archive_type=archive_type,
                    elapsed=f"{time.monotonic() - extract_started:.3f}s",
                )
                if archive_type == "unknown":
                    self._log_step("archive detect failed:", reason="unsupported magic signature")
                    await self._report_step(progress_callback, "Archive type is unsupported.")
                else:
                    await self._report_step(progress_callback, f"Detected {archive_type} archive; extracting...")

                extracted = await asyncio.to_thread(
                    self._extract_archive, archive_bytes, archive_path, extract_dir
                )
                if extracted:
                    return extracted
                extracted_count = await asyncio.to_thread(self._count_extracted_files, extract_dir)
                self._log_step(
                    "extract finish:",
                    archive_type=archive_type,
                    file_count=extracted_count,
                    elapsed=f"{time.monotonic() - extract_started:.3f}s",
                )
                await self._report_step(progress_callback, f"Extraction complete; {extracted_count} files unpacked.")

                find_started = time.monotonic()
                self._log_step("find save root start:", extract_dir=extract_dir, elapsed="0.000s")
                await self._report_step(progress_callback, "Finding save root in extracted files...")
                src_dir = await asyncio.to_thread(self._find_save_root, extract_dir)
                if src_dir is None:
                    self._log_step(
                        "find save root failed:",
                        elapsed=f"{time.monotonic() - find_started:.3f}s",
                    )
                    await self._report_step(progress_callback, "Could not find index.save/data.save in archive.")
                    return Resign007Result(
                        ok=False,
                        zip_bytes=None,
                        zip_filename=zip_filename,
                        summary_json=None,
                        stdout_tail="",
                        error="No index.save/data.save files found in archive",
                    )
                chosen_path = src_dir.relative_to(extract_dir) if src_dir != extract_dir else pathlib.Path(".")
                self._log_step(
                    "find save root finish:",
                    path=chosen_path,
                    elapsed=f"{time.monotonic() - find_started:.3f}s",
                )
                await self._report_step(progress_callback, f"Save root found at {chosen_path}.")

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
                self._log_step("build subprocess command:", command=shlex.join(cmd), elapsed="0.000s")
                await self._report_step(progress_callback, "Launching 007 resigner subprocess...")
                result = await self._run_process(cmd, new_id, progress_callback, timeout_seconds, dst_dir)
                self._log_step(
                    "run finish:",
                    ok=result.ok,
                    elapsed=f"{time.monotonic() - run_started:.3f}s",
                )
                return result
            except Exception as exc:
                self.log.exception("[savesign007] run_resign failed new_id=%s", new_id)
                await self._report_step(progress_callback, "007 resign failed unexpectedly.")
                return Resign007Result(False, None, zip_filename, None, "", str(exc))
            finally:
                self._log_step("temp dir cleaned:", path=tmpdir_path)

    def _extract_archive(
        self, archive_bytes: bytes, archive_path: pathlib.Path, extract_dir: pathlib.Path
    ) -> Resign007Result | None:
        magic = archive_bytes[:10]
        archive_type = self._detect_archive_type(archive_bytes)
        self._log_step("extract start:", archive_type=archive_type, path=archive_path)
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
            self.log.exception("[savesign007] extract failed archive_type=%s", archive_type)
            return Resign007Result(False, None, "", None, "", f"Failed to extract archive: {exc}")
        self._log_step("extract failed:", archive_type="unknown", magic=magic, size_bytes=len(archive_bytes))
        return Resign007Result(False, None, "", None, "", "Unsupported format")

    def _detect_archive_type(self, archive_bytes: bytes) -> str:
        if archive_bytes.startswith(b"Rar!\x1a\x07"):
            return "rar"
        if archive_bytes.startswith(b"7z\xbc\xaf\x27\x1c"):
            return "7z"
        if archive_bytes.startswith(b"PK\x03\x04") or archive_bytes.startswith(b"PK\x05\x06"):
            return "zip"
        return "unknown"

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

    def _count_extracted_files(self, root: pathlib.Path) -> int:
        return sum(1 for path in root.rglob("*") if path.is_file())

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
        process_started = time.monotonic()
        try:
            self._log_step("subprocess start:", timeout_seconds=timeout_seconds, elapsed="0.000s")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._log_step("subprocess pid:", pid=proc.pid, elapsed=f"{time.monotonic() - process_started:.3f}s")
            await self._report_step(progress_callback, f"Resigner started (pid {proc.pid}); waiting for output...")
            if proc.stdout is None:
                return Resign007Result(
                    False, None, zip_filename, None, "", "Failed to capture process output"
                )
            self._log_step(
                "subprocess wait configured:",
                timeout_seconds=timeout_seconds,
                elapsed=f"{time.monotonic() - process_started:.3f}s",
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
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await waiter
                reader.cancel()
                self._log_step(
                    "subprocess timeout:",
                    timeout_seconds=timeout_seconds,
                    elapsed=f"{time.monotonic() - process_started:.3f}s",
                )
                await self._report_step(progress_callback, f"Resigner timed out after {timeout_seconds}s.")
                return Resign007Result(
                    False,
                    None,
                    zip_filename,
                    None,
                    stdout_tail,
                    f"Resign timed out after {timeout_seconds}s",
                )
        except Exception as exc:
            self.log.exception("[savesign007] subprocess failed")
            await self._report_step(progress_callback, "Resigner subprocess failed to launch or run.")
            return Resign007Result(False, None, zip_filename, None, stdout_tail, str(exc))
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

        returncode = proc.returncode if proc else None
        self._log_step(
            "subprocess exit:",
            code=returncode,
            elapsed=f"{time.monotonic() - process_started:.3f}s",
            stdout_tail_length=len(stdout_tail),
        )
        summary = self._parse_summary(last_non_empty)
        summary_keys = len(summary) if summary else 0
        self._log_step("summary parse:", parsed=bool(summary), key_count=summary_keys)
        if returncode != 0:
            return Resign007Result(
                False,
                None,
                zip_filename,
                summary,
                stdout_tail,
                f"Resigner exited with code {returncode}",
            )

        zip_started = time.monotonic()
        self._log_step("zip output start:", root=dst_dir)
        await self._report_step(progress_callback, "Creating output zip archive...")
        zip_bytes = await asyncio.to_thread(self._zip_directory, dst_dir)
        entry_count = await asyncio.to_thread(self._count_extracted_files, dst_dir)
        self._log_step(
            "zip output finish:",
            bytes_count=len(zip_bytes),
            entry_count=entry_count,
            elapsed=f"{time.monotonic() - zip_started:.3f}s",
        )
        await self._report_step(progress_callback, f"Output zip ready with {entry_count} files.")
        return Resign007Result(
            True,
            zip_bytes,
            zip_filename,
            summary,
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
