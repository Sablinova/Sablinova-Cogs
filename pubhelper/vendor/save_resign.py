#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import traceback
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

SID_MIN = 76561197960265728
SID_MAX = 76561202255233023
ACCOUNT_OFFSET = 76561197960265728
ACCOUNT_INSTANCE = 0x0110000100000000
MAGIC = b"\x77\x65\x57\x60"
MAGIC_OFFSET = 0x0c
EXIT_BAD_ARGS = 2
EXIT_SAFETY = 3
EXIT_FORMAT = 4
EXIT_IO = 5
SAVE_NAMES = {"index.save": "index", "data.save": "data"}
BANNER = """============================================================
save_resign.py — local save file resigning utility
For LOCAL, OFFLINE, USER-OWNED saves only.
This tool rewrites schema-specific AccountID uint32 fields in
index.save files. Field 0x138 is preserved, and 0x00 is also
preserved by default unless --rewrite-0x00 is explicitly used.
This tool does NOT bypass online auth, DRM, anti-cheat, or
server-side validation. Modifying saves may violate the
game's Terms of Service. Use at your own risk.
============================================================"""


@dataclass(frozen=True)
class Schema:
    name: str
    pattern: re.Pattern
    size: int
    offsets: tuple[int, ...]


SCHEMAS = [
    Schema("KntProfileSaveFile", re.compile(r"^KntProfileSaveFile(?:-BCK-\d+)?$"), 390, (0x18, 0x140, 0x180)),
    Schema("LocalProfile", re.compile(r"^LocalProfile$"), 390, (0x18, 0x140, 0x180)),
    Schema("KntSlotSaveFile", re.compile(r"^KntSlotSaveFile-\d+$"), 382, (0x18, 0x140, 0x178)),
    Schema("SystemData", re.compile(r"^SystemData$"), 384, (0x18, 0x140)),
]


@dataclass
class IndexInfo:
    path: Path
    size: int
    sha256: str
    schema: Optional[Schema]
    magic_valid: bool
    account_ids: tuple[int, ...]
    consensus_account_id: Optional[int]
    value_0x138: Optional[int]
    value_0x00: Optional[int]


class SafetyError(Exception):
    pass


class FormatError(Exception):
    pass


class BadArgsError(Exception):
    pass


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def fail(code: int, message: str) -> int:
    eprint(f"ERROR: {message}")
    return code


def _emit_summary(args: argparse.Namespace, summary: dict) -> None:
    """Print the human-readable footer and, if --json, the JSON line.

    The JSON line is always the LAST line on stdout so callers (e.g. a Discord cog)
    can `tail -1` or read the final line to parse it.

    JSON summary shapes:
    - inspect: {"command","status","dir","source_steam64","consensus_account_ids",
      "derived_steam64_ids","index_files","data_files","all_match"}
    - resign: {"command","status","src","dst","dry_run","source_steam64",
      "target_steam64","files_total","files_planned","files_resigned",
      "files_copied","files_unchanged"}
    - verify: {"command","status","orig","resigned","source_steam64",
      "target_steam64","passed","failed","total"}
    """
    status = summary.get("status", "unknown")
    print("== Result ==")
    print(f"  command          : {summary.get('command', '')}")
    print(f"  status           : {status}")
    if "source_steam64" in summary:
        print(f"  source Steam64   : {summary.get('source_steam64') or '(none detected)'}")
    if "target_steam64" in summary:
        print(f"  target Steam64   : {summary.get('target_steam64') or '(n/a)'}")
    for key in ("src", "dst", "dir", "orig", "resigned"):
        if key in summary:
            print(f"  {key:<17}: {summary[key]}")
    for key in (
        "files_total",
        "files_planned",
        "files_copied",
        "files_resigned",
        "files_unchanged",
        "passed",
        "failed",
        "total",
        "index_files",
        "data_files",
        "all_match",
        "dry_run",
    ):
        if key in summary:
            print(f"  {key:<17}: {summary[key]}")
    if getattr(args, "json_output", False):
        import json as _json

        print(_json.dumps(summary, sort_keys=True, default=str))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_steam_id(value: str) -> int:
    s = value.strip()
    sid: Optional[int] = None
    if re.fullmatch(r"\d+", s):
        raw = int(s, 10)
        if raw < SID_MIN:
            if raw == 0:
                raise BadArgsError(f"account ID 0 is not valid: {value!r}")
            sid = ACCOUNT_INSTANCE | raw
        else:
            sid = raw
    elif re.fullmatch(r"0[xX][0-9a-fA-F]{1,16}", s):
        sid = int(s, 16)
    elif re.fullmatch(r"[0-9a-fA-F]{16}", s) and re.search(r"[a-fA-F]", s):
        sid = int(s, 16)
    else:
        match = re.fullmatch(r"\[?U:1:(\d+)\]?", s)
        if match:
            sid = ACCOUNT_INSTANCE | int(match.group(1), 10)
        else:
            match = re.fullmatch(r"STEAM_[01]:([01]):(\d+)", s)
            if match:
                y = int(match.group(1), 10)
                z = int(match.group(2), 10)
                sid = ACCOUNT_OFFSET + (2 * z) + y
    if sid is None or not (SID_MIN <= sid <= SID_MAX):
        raise BadArgsError(f"invalid Steam ID: {value!r}")
    return sid


def format_steam_id_variants(sid: int) -> Dict[str, str]:
    account_id = sid - ACCOUNT_OFFSET
    y = account_id & 1
    z = account_id // 2
    return {
        "decimal": str(sid),
        "hex": f"0x{sid:016x}",
        "U:1": f"[U:1:{account_id}]",
        "STEAM_0": f"STEAM_0:{y}:{z}",
    }


def format_hex_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)


def find_save_files(root: Path) -> Dict[str, List[Path]]:
    found = {"index": [], "data": [], "other": []}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        key = SAVE_NAMES.get(path.name, "other")
        found[key].append(path)
    return found


def account_id_from_sid(sid: int) -> int:
    account_id = sid - ACCOUNT_OFFSET
    if not (0 <= account_id <= 0xFFFFFFFF):
        raise BadArgsError(f"Steam ID out of AccountID uint32 range: {sid}")
    return account_id


def steam64_from_account_id(account_id: int) -> int:
    return ACCOUNT_OFFSET + account_id


def match_schema(parent_dir_name: str, size: int) -> Optional[Schema]:
    for schema in SCHEMAS:
        if schema.pattern.match(parent_dir_name) and schema.size == size:
            return schema
    return None


def require_magic(data: bytes, path: Path) -> None:
    if data[MAGIC_OFFSET:MAGIC_OFFSET + 4] != MAGIC:
        raise FormatError(f"{path}: magic mismatch at 0x0c")


def read_u32_le(data: bytes, offset: int) -> int:
    return struct.unpack("<I", data[offset:offset + 4])[0]


def read_u64_le(data: bytes, offset: int) -> int:
    return struct.unpack("<Q", data[offset:offset + 8])[0]


def read_index(path: Path) -> IndexInfo:
    data = path.read_bytes()
    schema = match_schema(path.parent.name, len(data))
    magic_valid = data[MAGIC_OFFSET:MAGIC_OFFSET + 4] == MAGIC
    account_ids = tuple(read_u32_le(data, offset) for offset in schema.offsets) if schema else ()
    consensus = account_ids[0] if account_ids and len(set(account_ids)) == 1 else None
    value_0x138 = read_u64_le(data, 0x138) if len(data) >= 0x140 else None
    value_0x00 = read_u32_le(data, 0x00) if len(data) >= 4 else None
    return IndexInfo(
        path=path,
        size=len(data),
        sha256=sha256_bytes(data),
        schema=schema,
        magic_valid=magic_valid,
        account_ids=account_ids,
        consensus_account_id=consensus,
        value_0x138=value_0x138,
        value_0x00=value_0x00,
    )


def safe_copy_tree(
    src: Path,
    dst: Path,
    transforms: Dict[Path, Callable[[bytes], bytes]],
    actions: Dict[Path, str],
    dry_run: bool,
    yes: bool,
) -> List[Tuple[Path, Path, str]]:
    plan: List[Tuple[Path, Path, str]] = []
    for source in sorted(p for p in src.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = source.relative_to(src)
        target = dst / rel
        action = actions.get(rel, "copy")
        plan.append((source, target, action))
    if dry_run:
        return plan
    staging_parent = dst.parent
    staging = Path(tempfile.mkdtemp(prefix=f".{dst.name}.staging-", dir=staging_parent))
    staged_root = staging / dst.name
    old_backup: Optional[Path] = None
    try:
        for source, target, action in plan:
            del action
            rel = target.relative_to(dst)
            staged_target = staged_root / rel
            staged_target.parent.mkdir(parents=True, exist_ok=True)
            transform = transforms.get(rel)
            if transform:
                staged_target.write_bytes(transform(source.read_bytes()))
            else:
                shutil.copy2(source, staged_target)
        if yes and dst.exists():
            if not dst.is_dir():
                raise OSError(f"destination exists and is not a directory: {dst}")
            old_backup = dst.parent / f".{dst.name}.old.{os.urandom(4).hex()}"
            os.rename(dst, old_backup)
        try:
            os.rename(staged_root, dst)
        except Exception:
            if old_backup is not None and old_backup.exists():
                try:
                    os.rename(old_backup, dst)
                except Exception:
                    pass
            raise
        if old_backup is not None and old_backup.exists():
            try:
                shutil.rmtree(old_backup)
            except Exception:
                pass
    except OSError as exc:
        raise OSError(f"I/O failure while writing staged tree: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return plan


def collect_index_infos(root: Path) -> List[IndexInfo]:
    return [read_index(path) for path in find_save_files(root)["index"]]


def autodetect_old_account_id(index_infos: List[IndexInfo]) -> int:
    if not index_infos:
        raise FormatError("no index.save files found")
    unknown = [str(info.path) for info in index_infos if info.schema is None]
    if unknown:
        raise FormatError("unrecognized index.save schema(s): " + ", ".join(unknown))
    bad_magic = [str(info.path) for info in index_infos if not info.magic_valid]
    if bad_magic:
        raise FormatError("magic mismatch in index.save file(s): " + ", ".join(bad_magic))
    missing = [str(info.path) for info in index_infos if info.consensus_account_id is None]
    if missing:
        raise FormatError("mixed AccountID values within index.save file(s): " + ", ".join(missing))
    account_ids = {info.consensus_account_id for info in index_infos if info.consensus_account_id is not None}
    if len(account_ids) == 1:
        return next(iter(account_ids))
    details = "\n".join(
        f"  {info.path}: {info.consensus_account_id if info.consensus_account_id is not None else info.account_ids}"
        for info in index_infos
    )
    raise FormatError(f"mixed AccountIDs found in source tree:\n{details}")


def validate_resign_paths(src: Path, dst: Path, yes: bool) -> None:
    src_resolved = src.resolve()
    dst_resolved = dst.resolve(strict=False)
    if src_resolved == dst_resolved:
        raise SafetyError("source and destination must differ")
    if src_resolved.is_relative_to(dst_resolved) or dst_resolved.is_relative_to(src_resolved):
        raise SafetyError("source and destination must not contain one another")
    if dst.exists() and any(dst.iterdir()) and not yes:
        raise SafetyError("destination exists and is non-empty; pass --yes to overwrite")


def preflight_index_infos(index_infos: List[IndexInfo], old_account_id: int, rewrite_0x00: bool) -> None:
    unknown = [str(info.path) for info in index_infos if info.schema is None]
    if unknown:
        raise FormatError("unrecognized index.save schema(s): " + ", ".join(unknown))
    bad_magic = [str(info.path) for info in index_infos if not info.magic_valid]
    if bad_magic:
        raise FormatError("magic mismatch in index.save file(s): " + ", ".join(bad_magic))
    for info in index_infos:
        assert info.schema is not None
        for offset, account_id in zip(info.schema.offsets, info.account_ids):
            if account_id != old_account_id:
                raise FormatError(
                    f"{info.path}: offset 0x{offset:x} expected AccountID {old_account_id}, found {account_id}"
                )
        if rewrite_0x00:
            expected = (old_account_id - 3) & 0xFFFFFFFF
            if info.value_0x00 != expected:
                found = "None" if info.value_0x00 is None else str(info.value_0x00)
                raise FormatError(f"{info.path}: offset 0x00 expected {expected}, found {found}")


def build_index_transform(rel: Path, schema: Schema, new_account_id: int, rewrite_0x00: bool) -> Callable[[bytes], bytes]:
    new_le = struct.pack("<I", new_account_id)
    new_0x00 = struct.pack("<I", (new_account_id - 3) & 0xFFFFFFFF)

    def transform(data: bytes) -> bytes:
        require_magic(data, rel)
        if len(data) != schema.size:
            raise FormatError(f"{rel}: size changed since preflight, expected {schema.size}, found {len(data)}")
        buf = bytearray(data)
        for offset in schema.offsets:
            buf[offset:offset + 4] = new_le
        if rewrite_0x00:
            buf[0:4] = new_0x00
        return bytes(buf)

    return transform


def format_account_line(offset: int, account_id: int) -> str:
    steam64 = steam64_from_account_id(account_id)
    return f"    0x{offset:03x}: account_id={account_id} steam64={steam64}"


def print_index_report(info: IndexInfo) -> None:
    schema_name = info.schema.name if info.schema else "UNKNOWN"
    print(f"== {info.path.parent.name}/{info.path.name} ==")
    print(f"  parent dir       : {info.path.parent.name}")
    print(f"  size             : {info.size} bytes")
    print(f"  sha256           : {info.sha256}")
    print(f"  schema           : {schema_name}")
    print(f"  magic valid      : {'yes' if info.magic_valid else 'no'}")
    print("  account fields   :")
    if info.schema:
        for offset, account_id in zip(info.schema.offsets, info.account_ids):
            print(format_account_line(offset, account_id))
    else:
        print("    (no schema match; offsets unknown)")
    if info.consensus_account_id is not None:
        steam64 = steam64_from_account_id(info.consensus_account_id)
        print(f"  consensus        : AccountID {info.consensus_account_id} Steam64 {steam64}")
    elif info.account_ids:
        print(f"  consensus        : WARN divergent AccountIDs {list(info.account_ids)}")
    else:
        print("  consensus        : none")
    preserved_138 = "None" if info.value_0x138 is None else f"0x{info.value_0x138:016x}"
    preserved_00 = "None" if info.value_0x00 is None else f"0x{info.value_0x00:08x}"
    print(f"  preserved 0x138  : {preserved_138}")
    print(f"  preserved 0x00   : {preserved_00}")
    if info.value_0x00 is None or info.consensus_account_id is None:
        note = "mismatch"
    else:
        note = "matches AccountID-3 pattern" if ((info.value_0x00 + 3) & 0xFFFFFFFF) == info.consensus_account_id else "mismatch"
    print(f"  0x00 pattern     : {note}")


def cmd_inspect(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    files = find_save_files(root)
    index_infos = [read_index(path) for path in files["index"]]
    for info in index_infos:
        print_index_report(info)
    print("== data.save files ==")
    for path in files["data"]:
        print(f"  {path.relative_to(root)} sha256={sha256_file(path)}")
    schema_counts: Dict[str, int] = {}
    consensus_ids = sorted({info.consensus_account_id for info in index_infos if info.consensus_account_id is not None})
    for info in index_infos:
        key = info.schema.name if info.schema else "UNKNOWN"
        schema_counts[key] = schema_counts.get(key, 0) + 1
    steam64_ids = [steam64_from_account_id(account_id) for account_id in consensus_ids]
    print("== Summary ==")
    print(f"  total index files : {len(files['index'])}")
    print(f"  total data files  : {len(files['data'])}")
    for name in sorted(key for key in schema_counts if key != 'UNKNOWN'):
        print(f"  schema {name:<16}: {schema_counts[name]}")
    print(f"  UNKNOWN           : {schema_counts.get('UNKNOWN', 0)}")
    print(f"  consensus AccountIDs: {consensus_ids if consensus_ids else '[]'}")
    print(f"  derived Steam64 IDs: {steam64_ids if steam64_ids else '[]'}")
    print(f"  all match         : {'yes' if len(consensus_ids) == 1 and index_infos else 'no'}")
    source_steam64 = steam64_ids[0] if len(steam64_ids) == 1 else None
    all_match = len(consensus_ids) == 1 and bool(index_infos)
    summary = {
        "command": "inspect",
        "status": "success" if all_match else "partial",
        "dir": str(root),
        "source_steam64": source_steam64,
        "consensus_account_ids": [str(x) for x in consensus_ids],
        "derived_steam64_ids": [str(x) for x in steam64_ids],
        "index_files": len(files["index"]),
        "data_files": len(files["data"]),
        "all_match": all_match,
    }
    _emit_summary(args, summary)
    return 0


def cmd_resign(args: argparse.Namespace) -> int:
    src = Path(args.src)
    dst = Path(args.dst)
    if not src.is_dir():
        raise BadArgsError(f"source directory does not exist or is not a directory: {src}")
    new_sid = parse_steam_id(args.new_id)
    new_account_id = account_id_from_sid(new_sid)
    validate_resign_paths(src, dst, args.yes)
    files = find_save_files(src)
    index_infos = [read_index(path) for path in files["index"]]
    old_account_id = account_id_from_sid(parse_steam_id(args.old_id)) if args.old_id else autodetect_old_account_id(index_infos)
    preflight_index_infos(index_infos, old_account_id, args.rewrite_0x00)
    transforms: Dict[Path, Callable[[bytes], bytes]] = {}
    actions: Dict[Path, str] = {}
    for info in index_infos:
        assert info.schema is not None
        rel = info.path.relative_to(src)
        transforms[rel] = build_index_transform(rel, info.schema, new_account_id, args.rewrite_0x00)
        actions[rel] = "resign-accountid+0x00" if args.rewrite_0x00 else "resign-accountid"
    plan = safe_copy_tree(src, dst, transforms, actions, args.dry_run, args.yes)
    for source, target, action in plan:
        print(f"{source} -> {target} [{action}]")
    files_total = len(plan)
    files_resigned = sum(1 for _, _, action in plan if "resign" in action.lower() or "rewrite" in action.lower())
    files_copied = sum(1 for _, _, action in plan if action.lower().startswith("copy"))
    files_unchanged = files_total - files_resigned - files_copied
    source_steam64 = steam64_from_account_id(old_account_id) if old_account_id is not None else None
    target_steam64 = steam64_from_account_id(new_account_id)
    summary = {
        "command": "resign",
        "status": "success" if not args.dry_run else "planned",
        "src": str(args.src),
        "dst": str(args.dst),
        "dry_run": bool(args.dry_run),
        "source_steam64": source_steam64,
        "target_steam64": target_steam64,
        "files_total": files_total,
        "files_planned": files_total,
        "files_resigned": files_resigned,
        "files_copied": files_copied,
        "files_unchanged": files_unchanged,
    }
    _emit_summary(args, summary)
    return 0


def verify_index_pair(
    orig: Path,
    resigned: Path,
    old_account_id: int,
    new_account_id: int,
    rewrite_0x00: bool,
) -> Tuple[bool, str]:
    left = orig.read_bytes()
    right = resigned.read_bytes()
    if len(left) != len(right):
        return False, "size changed"
    schema = match_schema(orig.parent.name, len(left))
    if schema is None:
        return False, "unknown schema"
    if match_schema(resigned.parent.name, len(right)) != schema:
        return False, "schema mismatch"
    try:
        require_magic(left, orig)
        require_magic(right, resigned)
    except FormatError as exc:
        return False, str(exc)
    diffs = {index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]}
    allowed = {i for offset in schema.offsets for i in range(offset, offset + 4)}
    if rewrite_0x00:
        allowed.update(range(0, 4))
    disallowed = sorted(index for index in diffs if index not in allowed)
    if disallowed:
        return False, f"bytes changed outside allowed regions: {disallowed[:8]}"
    for offset in schema.offsets:
        if read_u32_le(left, offset) != old_account_id:
            return False, f"orig offset 0x{offset:x} does not match old AccountID"
        if read_u32_le(right, offset) != new_account_id:
            return False, f"resigned offset 0x{offset:x} does not match new AccountID"
    if left[0x138:0x140] != right[0x138:0x140]:
        return False, "0x138..0x140 changed"
    if right[0x138:0x13c] == struct.pack("<I", new_account_id):
        return False, "new AccountID bytes appear at 0x138"
    if not rewrite_0x00 and left[0:4] != right[0:4]:
        return False, "0x00 changed without --rewrite-0x00"
    if rewrite_0x00:
        old_expected = struct.pack("<I", (old_account_id - 3) & 0xFFFFFFFF)
        new_expected = struct.pack("<I", (new_account_id - 3) & 0xFFFFFFFF)
        if left[0:4] != old_expected:
            return False, "orig 0x00 does not match old AccountID-3"
        if right[0:4] != new_expected:
            return False, "resigned 0x00 does not match new AccountID-3"
    return True, "PASS"


def cmd_verify(args: argparse.Namespace) -> int:
    orig = Path(args.orig)
    resigned = Path(args.resigned)
    if not orig.is_dir():
        raise BadArgsError(f"source directory does not exist or is not a directory: {orig}")
    if not resigned.is_dir():
        raise BadArgsError(f"source directory does not exist or is not a directory: {resigned}")
    new_account_id = account_id_from_sid(parse_steam_id(args.new_id))
    orig_indexes = collect_index_infos(orig)
    old_account_id = (
        account_id_from_sid(parse_steam_id(args.old_id)) if args.old_id else autodetect_old_account_id(orig_indexes)
    )
    orig_files = find_save_files(orig)
    new_files = find_save_files(resigned)
    orig_rel = sorted(path.relative_to(orig) for group in orig_files.values() for path in group)
    new_rel = sorted(path.relative_to(resigned) for group in new_files.values() for path in group)
    source_steam64 = steam64_from_account_id(old_account_id) if old_account_id is not None else None
    target_steam64 = steam64_from_account_id(new_account_id)
    if orig_rel != new_rel:
        print("FAIL tree contents differ")
        summary = {
            "command": "verify",
            "status": "failed",
            "reason": "tree contents differ",
            "orig": str(args.orig),
            "resigned": str(args.resigned),
            "source_steam64": source_steam64,
            "target_steam64": target_steam64,
            "passed": 0,
            "failed": 0,
            "total": 0,
        }
        _emit_summary(args, summary)
        return EXIT_FORMAT
    passed = 0
    total = 0
    for rel in orig_rel:
        total += 1
        left = orig / rel
        right = resigned / rel
        if rel.name == "data.save":
            ok = sha256_file(left) == sha256_file(right)
            reason = "data.save sha256"
        elif rel.name == "index.save":
            ok, reason = verify_index_pair(left, right, old_account_id, new_account_id, args.rewrite_0x00)
        else:
            ok = sha256_file(left) == sha256_file(right)
            reason = "copied unchanged"
        print(f"{'PASS' if ok else 'FAIL'} {rel} {reason}")
        passed += 1 if ok else 0
    print(f"Aggregate: {passed}/{total} PASS")
    status = "success" if passed == total and total > 0 else ("failed" if total > 0 else "empty")
    summary = {
        "command": "verify",
        "status": status,
        "orig": str(args.orig),
        "resigned": str(args.resigned),
        "source_steam64": source_steam64,
        "target_steam64": target_steam64,
        "passed": passed,
        "failed": total - passed,
        "total": total,
    }
    _emit_summary(args, summary)
    return 0 if passed == total else EXIT_FORMAT


def build_parser() -> argparse.ArgumentParser:
    description = (
        "Inspect, resign, and verify local 007 First Light save trees.\n\n"
        "Examples:\n"
        "  Inspect a save tree:\n"
        "    save_resign.py inspect /path/to/remote\n\n"
        "  Dry-run a resign:\n"
        "    save_resign.py resign --src ./remote --dst ./out --new-id 76561198000000001 --dry-run\n\n"
        "  Resign for real:\n"
        "    save_resign.py resign --src ./remote --dst ./out --new-id [U:1:775667115]\n\n"
        "  Accepted Steam ID formats:\n"
        "    76561198735932843              (Steam64 decimal)\n"
        "    0x011000012e3bbdab             (Steam64 hex)\n"
        "    [U:1:775667115] or U:1:775667115  (SteamID3)\n"
        "    775667115                      (Account ID — auto-wrapped to Steam64)\n"
        "    STEAM_0:1:387833557            (Legacy)"
    )
    parser = argparse.ArgumentParser(
        prog="save_resign.py",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit a machine-readable JSON summary on the final line of stdout",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="inspect a save tree and report index/data metadata")
    inspect_parser.add_argument("dir", help="save tree root directory")
    inspect_parser.set_defaults(func=cmd_inspect)

    resign_parser = subparsers.add_parser("resign", help="copy a save tree and rewrite AccountID fields in index.save files")
    resign_parser.add_argument("--src", required=True, help="source save tree")
    resign_parser.add_argument("--dst", required=True, help="destination save tree")
    resign_parser.add_argument("--new-id", required=True, help="new Steam ID in any supported format")
    resign_parser.add_argument("--old-id", help="expected old Steam ID; auto-detected if omitted")
    resign_parser.add_argument("--dry-run", action="store_true", help="print the copy/resign plan without writing files")
    resign_parser.add_argument("--yes", action="store_true", help="allow replacing a non-empty destination")
    resign_parser.add_argument("--rewrite-0x00", action="store_true", help="also rewrite 0x00 as AccountID-3")
    resign_parser.set_defaults(func=cmd_resign)

    verify_parser = subparsers.add_parser("verify", help="compare an original and resigned save tree")
    verify_parser.add_argument("--orig", required=True, help="original source save tree")
    verify_parser.add_argument("--resigned", required=True, help="resigned destination save tree")
    verify_parser.add_argument("--new-id", required=True, help="new Steam ID in any supported format")
    verify_parser.add_argument("--old-id", help="expected old Steam ID; auto-detected from --orig if omitted")
    verify_parser.add_argument("--rewrite-0x00", action="store_true", help="verify 0x00 was rewritten as AccountID-3")
    verify_parser.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    eprint(BANNER)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except BrokenPipeError:
        return 0
    except SafetyError as exc:
        return fail(EXIT_SAFETY, str(exc))
    except FormatError as exc:
        return fail(EXIT_FORMAT, str(exc))
    except BadArgsError as exc:
        return fail(EXIT_BAD_ARGS, str(exc))
    except ValueError as exc:
        return fail(EXIT_BAD_ARGS, str(exc))
    except OSError as exc:
        return fail(EXIT_IO, str(exc))
    except Exception:
        eprint(traceback.format_exc())
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
