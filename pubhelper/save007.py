import asyncio
import contextlib
import inspect
import io
import logging
import os
import pathlib
import re
import stat
import shutil
import tempfile
import time
import uuid
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
    # VDF generation outcome (only meaningful when vdf=True was requested).
    # Values: "skipped" (vdf=no), "ok", "failed".
    vdf_status: str = "skipped"
    vdf_message: str | None = None


class Save007Resigner:
    def __init__(self, log: logging.Logger):
        self.log = log

    # 007 First Light Steam App ID. Documented here for traceability; the
    # upstream VDF generator infers metadata from the `remote/` folder contents
    # and does not require an App ID flag, so this constant is informational.
    APP_ID_007_FIRST_LIGHT = 3768760

    @property
    def vendor_bin(self) -> pathlib.Path:
        return (pathlib.Path(__file__).parent / "vendor" / "bin" / "sabby007").resolve()

    @property
    def vdf_vendor_bin(self) -> pathlib.Path:
        return (
            pathlib.Path(__file__).parent / "vendor" / "bin" / "remotecache_vdf_gen"
        ).resolve()

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
        progress_callback: ProgressCallback,
        dry_run: bool = False,
        timeout_seconds: int = 600,
        vdf: bool = False,
    ) -> Resign007Result:
        if dry_run:
            raise ValueError("Dry run is no longer supported by the sabby007 engine")

        zip_filename = self._zip_filename(new_id)
        run_started = time.monotonic()
        self._log_step(
            "run start:",
            new_id=new_id,
            archive_bytes=len(archive_bytes),
            elapsed="0.000s",
            vdf_requested=vdf,
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

                self._validate_new_id(new_id)

                copy_started = time.monotonic()
                self._log_step("copy source tree start:", src=src_dir, dst=dst_dir)
                await self._report_step(progress_callback, "Copying extracted save tree before resign...")
                await asyncio.to_thread(
                    shutil.copytree,
                    src_dir,
                    dst_dir,
                    dirs_exist_ok=True,
                    symlinks=True,
                )
                backup_restore_name = self._prepare_backup_conflict(dst_dir)
                self._log_step(
                    "copy source tree finish:",
                    elapsed=f"{time.monotonic() - copy_started:.3f}s",
                )

                vendor_bin = self.vendor_bin
                self._ensure_vendor_bin(vendor_bin)

                cmd = [
                    str(vendor_bin),
                    "resign",
                    "--folder",
                    str(dst_dir),
                    "--to-id",
                    new_id,
                    "-y",
                ]
                self._log_step("build subprocess command:", command=cmd, elapsed="0.000s")
                await self._report_step(progress_callback, "Launching 007 resigner subprocess...")
                result = await self._run_process(
                    cmd,
                    new_id,
                    progress_callback,
                    timeout_seconds,
                    dst_dir,
                    backup_restore_name,
                    vdf=vdf,
                )
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
                self._ensure_rar_support()
                with rarfile.RarFile(archive_path) as archive:
                    self._safe_extract_rar(archive, extract_dir)
                self._ensure_no_symlinks(extract_dir)
                return None
            if archive_bytes.startswith(b"7z\xbc\xaf\x27\x1c"):
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    self._safe_extract_7z(archive, extract_dir)
                self._ensure_no_symlinks(extract_dir)
                return None
            if archive_bytes.startswith(b"PK\x03\x04") or archive_bytes.startswith(b"PK\x05\x06"):
                with zipfile.ZipFile(archive_path, "r") as archive:
                    self._safe_extract_zip(archive, extract_dir)
                self._ensure_no_symlinks(extract_dir)
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

    def _ensure_vendor_bin(self, vendor_bin: pathlib.Path) -> None:
        if vendor_bin.exists() and os.access(vendor_bin, os.X_OK):
            return
        raise RuntimeError(
            f"sabby007 binary is missing or not executable at {vendor_bin}. "
            "Ensure pubhelper/vendor/bin/sabby007 is committed with +x bit "
            "(git update-index --chmod=+x)."
        )

    def _ensure_rar_support(self) -> None:
        unrar = shutil.which("unrar")
        unar = shutil.which("unar")
        bsdtar = shutil.which("bsdtar")
        if unrar:
            rarfile.UNRAR_TOOL = unrar
        elif unar:
            rarfile.UNAR_TOOL = unar
        elif bsdtar:
            rarfile.BSDTAR_TOOL = bsdtar
        else:
            raise RuntimeError(
                "RAR extraction requires `unrar`, `unar`, or `bsdtar` on the bot host. "
                "Install with: `sudo apt install unrar` (or `unar`)."
            )

    def _safe_extract_zip(self, archive: zipfile.ZipFile, extract_dir: pathlib.Path) -> None:
        for member in archive.infolist():
            self._ensure_zip_member_safe(member)
            self._validate_extract_path(extract_dir, member.filename)
        archive.extractall(extract_dir)

    def _safe_extract_rar(self, archive: rarfile.RarFile, extract_dir: pathlib.Path) -> None:
        for member in archive.infolist():
            self._ensure_rar_member_safe(member)
            self._validate_extract_path(extract_dir, member.filename)
        archive.extractall(extract_dir)

    def _safe_extract_7z(self, archive: py7zr.SevenZipFile, extract_dir: pathlib.Path) -> None:
        for member in archive.list():
            self._ensure_7z_member_safe(member)
        for name in archive.getnames():
            self._validate_extract_path(extract_dir, name)
        archive.extractall(extract_dir)

    def _ensure_zip_member_safe(self, member: zipfile.ZipInfo) -> None:
        if stat.S_ISLNK(member.external_attr >> 16):
            raise RuntimeError("Archive contains symlinks; refusing to extract for safety.")

    def _ensure_rar_member_safe(self, member: rarfile.RarInfo) -> None:
        is_symlink = False
        with contextlib.suppress(AttributeError):
            is_symlink = bool(member.is_symlink())
        if not is_symlink and (getattr(member, "file_attr", 0) & 0xF000) == 0xA000:
            is_symlink = True
        if is_symlink:
            raise RuntimeError("Archive contains symlinks; refusing to extract for safety.")

    def _ensure_7z_member_safe(self, member: object) -> None:
        if getattr(member, "is_symlink", False):
            raise RuntimeError("Archive contains symlinks; refusing to extract for safety.")

    def _ensure_no_symlinks(self, root: pathlib.Path) -> None:
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            base = pathlib.Path(current_root)
            for name in [*dirnames, *filenames]:
                if (base / name).is_symlink():
                    raise RuntimeError("Archive contains symlinks; refusing to extract for safety.")

    def _validate_new_id(self, new_id: str) -> None:
        if not re.fullmatch(r"[0-9]{1,20}", new_id or ""):
            raise ValueError(f"Invalid Steam64 id for --to-id: {new_id!r}")

    def _prepare_backup_conflict(self, dst_dir: pathlib.Path) -> pathlib.Path | None:
        backup_dir = dst_dir / "Backup"
        if not backup_dir.exists():
            return None
        sentinel = dst_dir / f"_user_backup_{uuid.uuid4().hex}"
        backup_dir.rename(sentinel)
        return sentinel

    def _restore_user_backup(self, dst_dir: pathlib.Path, sentinel: pathlib.Path | None) -> None:
        if sentinel is None or not sentinel.exists():
            return
        restored = dst_dir / "Backup"
        if restored.exists():
            raise RuntimeError("Resign engine left a conflicting Backup folder after cleanup.")
        sentinel.rename(restored)

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
        backup_restore_name: pathlib.Path | None,
        vdf: bool = False,
    ) -> Resign007Result:
        stdout_tail = ""
        zip_filename = self._zip_filename(new_id)
        proc = None
        process_started = time.monotonic()
        try:
            self._log_step("subprocess start:", timeout_seconds=timeout_seconds, elapsed="0.000s")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except OSError as exc:
                if exc.errno in (8, 13) or "GLIBC" in str(exc):
                    raise RuntimeError(
                        "sabby007 binary failed to execute. The bot host may have an incompatible "
                        "GLIBC (binary requires 2.34+, Debian 12 / Ubuntu 22.04+). "
                        f"Original error: {exc}"
                    ) from exc
                raise
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
                stdout_tail = await asyncio.wait_for(reader, timeout=timeout_seconds)
                elapsed = time.monotonic() - started
                remaining = max(1.0, timeout_seconds - elapsed)
                await asyncio.wait_for(waiter, timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
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
                with contextlib.suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)

        returncode = proc.returncode if proc else None
        self._log_step(
            "subprocess exit:",
            code=returncode,
            elapsed=f"{time.monotonic() - process_started:.3f}s",
            stdout_tail_length=len(stdout_tail),
        )
        summary = self._synthesize_summary(dst_dir, returncode, stdout_tail)
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

        await asyncio.to_thread(shutil.rmtree, dst_dir / "Backup", ignore_errors=True)
        await asyncio.to_thread(self._restore_user_backup, dst_dir, backup_restore_name)

        vdf_status = "skipped"
        vdf_message: str | None = None
        vdf_bytes: bytes | None = None
        if vdf:
            vdf_bytes, vdf_message = await self._generate_vdf(
                dst_dir=dst_dir,
                new_id=new_id,
                progress_callback=progress_callback,
                timeout_seconds=min(120, max(30, timeout_seconds // 4)),
            )
            if vdf_bytes is not None:
                vdf_status = "ok"
                vdf_message = None
            else:
                vdf_status = "failed"
                # vdf_message already set by _generate_vdf

        zip_started = time.monotonic()
        self._log_step(
            "zip output start:",
            root=dst_dir,
            vdf_status=vdf_status,
            vdf_size=len(vdf_bytes) if vdf_bytes else 0,
        )
        await self._report_step(progress_callback, "Creating output zip archive...")
        zip_bytes = await asyncio.to_thread(self._zip_directory, dst_dir, vdf_bytes)
        entry_count = await asyncio.to_thread(self._count_extracted_files, dst_dir)
        self._log_step(
            "zip output finish:",
            bytes_count=len(zip_bytes),
            entry_count=entry_count,
            vdf_included=bool(vdf_bytes),
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
            vdf_status=vdf_status,
            vdf_message=vdf_message,
        )

    async def _read_stdout(
        self, stdout: asyncio.StreamReader, progress_callback: ProgressCallback
    ) -> str:
        buffer = b""
        tail = ""
        while True:
            chunk = await stdout.read(1024)
            if not chunk:
                break
            buffer += chunk
            buffer, tail = await self._consume_buffer(buffer, tail, progress_callback)
        if buffer:
            line = buffer.decode("utf-8", errors="ignore").strip()
            tail = self._append_tail(tail, line)
            if line:
                await self._emit_progress(progress_callback, line)
        return tail

    async def _consume_buffer(
        self,
        buffer: bytes,
        tail: str,
        progress_callback: ProgressCallback,
    ) -> tuple[bytes, str]:
        while True:
            newline = buffer.find(b"\n")
            carriage = buffer.find(b"\r")
            indexes = [idx for idx in (newline, carriage) if idx != -1]
            if not indexes:
                return buffer, tail
            idx = min(indexes)
            line = buffer[:idx].decode("utf-8", errors="ignore").strip()
            buffer = buffer[idx + 1 :]
            tail = self._append_tail(tail, line)
            if line:
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

    def _synthesize_summary(self, dst_dir: pathlib.Path, exit_code: int | None, stdout_tail: str) -> dict:
        files = {"index": 0, "data": 0, "copied": 0, "skipped": 0}
        containers = 0
        for path in dst_dir.rglob("*"):
            if not path.is_file() or "Backup" in path.parts:
                continue
            files["copied"] += 1
            if path.name == "index.save":
                files["index"] += 1
                containers += 1
            elif path.name == "data.save":
                files["data"] += 1
        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "files": files,
            "containers": containers,
            "changed": None,
            "from_id": "unknown",
            "to_id": "unknown",
            "stdout_tail": stdout_tail[-4000:],
            # Safe defaults for legacy consumers expecting JSON-engine keys.
        }

    async def _generate_vdf(
        self,
        dst_dir: pathlib.Path,
        new_id: str,
        progress_callback: ProgressCallback,
        timeout_seconds: int,
    ) -> tuple[bytes | None, str | None]:
        """Generate ``remotecache.vdf`` for 007 First Light using the vendored
        Linux binary from RemoteCacheVdfGenerator.

        Returns ``(vdf_bytes, None)`` on success or ``(None, error_message)``
        on any failure (missing binary, non-zero exit, timeout, missing
        runtime, output not produced). NEVER raises -- the caller relies on
        the soft-fail policy so the resigned save zip is still delivered.

        CLI invocation (per upstream README):
            ./remote-cache-vdf-generator -p <path-to-remote-folder>

        Critically, upstream parses the App ID from the parent directory
        name of the supplied remote path (see ``GetAppIdFromPath`` in
        ``Models/RemoteCacheVdfFile.cs``):

            var parent = Path.GetFileName(Path.GetDirectoryName(path));
            return int.TryParse(parent, out var result) ...

        i.e. the immediate parent of ``remote`` MUST be a directory whose
        name parses as an integer. Passing the bare resigned ``remote/``
        folder yields ``Invalid AppId in path`` and exit code -6. We
        therefore stage a Steam-userdata-like layout under a private
        workdir:

            <workdir>/<APP_ID_007_FIRST_LIGHT>/remote -> dst_dir (symlink,
                                                       falling back to copytree)

        and invoke the binary with ``-p <workdir>/<APP_ID>/remote``.

        The binary writes ``remotecache.vdf`` into its own directory
        (``MyAppInfo.RootPath``), so we copy the binary into the workdir
        first and read the produced vdf from there. The resigned save tree
        is left untouched.
        """
        vdf_started = time.monotonic()
        vendor_bin = self.vdf_vendor_bin
        self._log_step(
            "vdf gen start:",
            new_id=new_id,
            app_id=self.APP_ID_007_FIRST_LIGHT,
            remote=dst_dir,
            binary=vendor_bin,
            elapsed="0.000s",
        )
        await self._report_step(
            progress_callback, "Generating remotecache.vdf for 007 First Light..."
        )

        # Preflight: binary present and executable.
        if not vendor_bin.exists() or not os.access(vendor_bin, os.X_OK):
            msg = (
                f"VDF binary missing or not executable at {vendor_bin}. "
                "Bot host must redeploy with vendor/bin/remotecache_vdf_gen +x."
            )
            self._log_step(
                "vdf gen failed:",
                reason="binary missing or not executable",
                binary=vendor_bin,
                exists=vendor_bin.exists(),
            )
            await self._report_step(progress_callback, f"VDF skipped: {msg}")
            return None, msg

        with tempfile.TemporaryDirectory(prefix="vdfgen-") as vdf_workdir:
            workdir_path = pathlib.Path(vdf_workdir)
            # Copy the binary into the workdir so the produced vdf lands here
            # (the upstream tool writes next to its executable, so isolating
            # the workdir keeps the resigned save tree untouched).
            run_bin = workdir_path / vendor_bin.name
            try:
                await asyncio.to_thread(shutil.copy2, str(vendor_bin), str(run_bin))
                await asyncio.to_thread(
                    os.chmod, str(run_bin), 0o755
                )
            except Exception as exc:
                msg = f"Failed to stage VDF binary: {exc}"
                self._log_step("vdf gen failed:", reason=msg)
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg

            # Stage a Steam-userdata-style layout: <workdir>/<APP_ID>/remote.
            # Upstream parses AppId from the parent directory name of the
            # supplied path, so the immediate parent of `remote` must be the
            # integer App ID. Prefer a symlink to avoid copying the whole
            # save tree; fall back to a full copy if symlinking is not
            # permitted on the host filesystem (e.g. Windows w/o privs,
            # restricted mounts).
            app_id_dir = workdir_path / str(self.APP_ID_007_FIRST_LIGHT)
            staged_remote = app_id_dir / "remote"
            stage_mode = "symlink"
            try:
                await asyncio.to_thread(app_id_dir.mkdir, parents=True, exist_ok=False)
                try:
                    await asyncio.to_thread(
                        os.symlink, str(dst_dir), str(staged_remote), True
                    )
                except (OSError, NotImplementedError) as exc:
                    self._log_step(
                        "vdf gen stage symlink failed; falling back to copy:",
                        reason=str(exc),
                    )
                    stage_mode = "copytree"
                    await asyncio.to_thread(
                        shutil.copytree,
                        str(dst_dir),
                        str(staged_remote),
                        symlinks=False,
                    )
            except Exception as exc:
                msg = f"Failed to stage AppId remote layout: {exc}"
                self._log_step("vdf gen failed:", reason=msg)
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg

            self._log_step(
                "vdf gen stage layout:",
                app_id=self.APP_ID_007_FIRST_LIGHT,
                staged_remote=staged_remote,
                stage_mode=stage_mode,
            )

            cmd = [str(run_bin), "-p", str(staged_remote)]
            self._log_step(
                "vdf gen subprocess:",
                command=cmd,
                workdir=workdir_path,
                timeout_seconds=timeout_seconds,
            )

            proc = None
            stdout_bytes = b""
            stderr_bytes = b""
            try:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=str(workdir_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except OSError as exc:
                    msg = (
                        f"VDF binary failed to launch: {exc}. "
                        "Bot host may lack .NET 10 runtime or compatible glibc."
                    )
                    self._log_step("vdf gen failed:", reason=msg)
                    await self._report_step(progress_callback, f"VDF skipped: {msg}")
                    return None, msg

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_seconds
                    )
                except asyncio.TimeoutError:
                    with contextlib.suppress(Exception):
                        proc.kill()
                    with contextlib.suppress(asyncio.TimeoutError, Exception):
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    msg = f"VDF generator timed out after {timeout_seconds}s"
                    self._log_step("vdf gen failed:", reason="timeout", timeout=timeout_seconds)
                    await self._report_step(progress_callback, f"VDF skipped: {msg}")
                    return None, msg
            except Exception as exc:
                self.log.exception("[savesign007] vdf subprocess crashed")
                msg = f"VDF subprocess error: {exc}"
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg
            finally:
                if proc and proc.returncode is None:
                    with contextlib.suppress(Exception):
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=5)

            returncode = proc.returncode if proc else None
            stdout_tail = stdout_bytes.decode("utf-8", errors="ignore")[-1000:]
            stderr_tail = stderr_bytes.decode("utf-8", errors="ignore")[-1000:]
            self._log_step(
                "vdf gen subprocess exit:",
                code=returncode,
                stdout_len=len(stdout_bytes),
                stderr_len=len(stderr_bytes),
                elapsed=f"{time.monotonic() - vdf_started:.3f}s",
            )

            # The .NET apphost can print a "You must install .NET to run this
            # application." banner and STILL exit 0 -- detect that explicitly.
            runtime_missing_marker = "You must install .NET"
            combined_output = f"{stdout_tail}\n{stderr_tail}"
            if runtime_missing_marker in combined_output:
                msg = (
                    "VDF generator requires the .NET 10 runtime on the bot host "
                    "(install dotnet-runtime-10.0). Resigned zip still delivered without VDF."
                )
                self._log_step("vdf gen failed:", reason="dotnet runtime missing")
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg

            if returncode != 0:
                # Surface the tail of whatever the binary emitted -- upstream
                # prints errors like "Invalid AppId in path" to stdout, so we
                # prefer stderr but fall back to stdout. Full tails go to logs;
                # the user-facing message is bounded to ~200 chars of tail so
                # the overall message stays under the caller's 300-char cap.
                combined = "\n".join(
                    part for part in (stderr_tail, stdout_tail) if part
                ).strip()
                # Strip ANSI escape sequences (the .NET console helper emits
                # colour codes) so the embed/text stays readable.
                clean_tail = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", combined)
                # Collapse whitespace and drop the apphost banner lines if any
                # snuck through; users only care about the actual error text.
                clean_tail = re.sub(r"\s+", " ", clean_tail).strip()
                snippet = clean_tail[-200:] if clean_tail else ""
                msg = (
                    f"VDF generator exited with code {returncode}"
                    + (f": {snippet}" if snippet else "")
                )
                self._log_step(
                    "vdf gen failed:",
                    reason="non-zero exit",
                    code=returncode,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                )
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg

            vdf_path = workdir_path / "remotecache.vdf"
            try:
                if not vdf_path.exists():
                    msg = (
                        "VDF generator exited 0 but did not produce remotecache.vdf "
                        "in its working directory."
                    )
                    self._log_step("vdf gen failed:", reason="output missing", path=vdf_path)
                    await self._report_step(progress_callback, f"VDF skipped: {msg}")
                    return None, msg
                vdf_bytes = await asyncio.to_thread(vdf_path.read_bytes)
            except Exception as exc:
                msg = f"Failed to read produced remotecache.vdf: {exc}"
                self._log_step("vdf gen failed:", reason=msg)
                await self._report_step(progress_callback, f"VDF skipped: {msg}")
                return None, msg

            self._log_step(
                "vdf gen finish:",
                bytes_count=len(vdf_bytes),
                elapsed=f"{time.monotonic() - vdf_started:.3f}s",
            )
            await self._report_step(
                progress_callback,
                f"remotecache.vdf generated ({len(vdf_bytes)} bytes).",
            )
            return vdf_bytes, None

    def _zip_directory(
        self, root: pathlib.Path, vdf_bytes: bytes | None = None
    ) -> bytes:
        """Zip the resigned save tree.

        When ``vdf_bytes`` is provided, the archive is laid out as:
            remote/<resigned tree...>
            remotecache.vdf
        i.e. the resigned files live under a ``remote/`` directory and the
        ``remotecache.vdf`` sits beside it at the archive root.

        When ``vdf_bytes`` is ``None`` (legacy / vdf=no), the resigned tree
        is zipped at the archive root (preserving the historical layout).
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            if vdf_bytes is not None:
                # Place every resigned file under remote/<relpath>.
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        rel = path.relative_to(root)
                        arcname = pathlib.PurePosixPath("remote") / pathlib.PurePosixPath(*rel.parts)
                        archive.writestr(str(arcname), path.read_bytes())
                # remotecache.vdf at the archive root, beside remote/.
                archive.writestr("remotecache.vdf", vdf_bytes)
            else:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root))
        return buffer.getvalue()
