#!/usr/bin/env python3
"""007 First Light save tools, version 2.1.0."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path

STEAM64_BASE = 76561197960265728
INDEX_HEADER = b"SSaveGameHeader"
DATA_PREAMBLE = b"\x03\x00\x00\x00"
ACCOUNT_HIGH_BYTES = b"\x01\x00\x10\x01"
STEAM_HIGH_BYTES = b"\x01\x00\x10\x01"
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_SAFETY = 3
EXIT_FORMAT = 4
EXIT_IO = 5
__version__ = "2.1.0"
KNOWN_NAMES = ["Version", "Spawnpoint", "PlayerName", "Difficulty", "Mission", "Checkpoint", "Score", "Time", "Health"]


def steam64_to_le8(sid: int) -> bytes:
    """Return Steam64 as 8-byte little-endian."""
    return sid.to_bytes(8, "little")


def le8_to_steam64(data: bytes) -> int:
    """Return Steam64 integer from 8-byte little-endian."""
    return int.from_bytes(data, "little")


def xor_stream(buf: bytes, key8: bytes) -> bytes:
    """XOR a buffer with a repeating 8-byte key."""
    return bytes(b ^ key8[i & 7] for i, b in enumerate(buf))


def account_id_to_steam64(aid: int) -> int:
    """Convert AccountID to Steam64."""
    return STEAM64_BASE + aid


def steam64_to_account_id(sid: int) -> int:
    """Convert Steam64 to AccountID."""
    return sid - STEAM64_BASE


def fail(message: str, code: int = EXIT_FORMAT) -> RuntimeError:
    return RuntimeError(f"{code}:{message}")


def out(args: argparse.Namespace, text: str, error: bool = False) -> None:
    stream = sys.stderr if args.json or error else sys.stdout
    print(text, file=stream)


def summary(args: argparse.Namespace, data: dict) -> None:
    if args.json:
        print(json.dumps(data, sort_keys=True), flush=True)
        return
    print("== Result ==")
    print(f"command: {data.get('command', 'N/A')}")
    print(f"status: {data.get('status', 'error')}")
    print(f"source steam64: {data.get('source_steam64', 'N/A')}")
    print(f"target steam64: {data.get('target_steam64', 'N/A')}")
    counts = data.get("files", {})
    print(f"files: index={counts.get('index', 0)} data={counts.get('data', 0)} copied={counts.get('copied', 0)} skipped={counts.get('skipped', 0)}")
def parse_steam_id(value: str) -> int:
    """Parse a decimal Steam64 string."""
    sid = int(value)
    if sid < STEAM64_BASE:
        sid = account_id_to_steam64(sid)
    return sid


def detect_steam_id_from_index(path: Path) -> int | None:
    """Detect Steam64 from index.save using known plaintext and fixed high bytes."""
    for candidate in [path, path.parent / "Backup" / "index.save", path.parent / "index.save.backup"]:
        try:
            cipher = candidate.read_bytes()
        except OSError:
            continue
        if len(cipher) < 24:
            continue
        low4 = bytes(cipher[i] ^ DATA_PREAMBLE[i] for i in range(4))
        key8 = low4 + STEAM_HIGH_BYTES
        plain_9_24 = bytes(cipher[9 + i] ^ key8[(9 + i) & 7] for i in range(15))
        if plain_9_24 != INDEX_HEADER:
            continue
        return le8_to_steam64(key8)
    return None


def brute_force_data_key(path: Path) -> int | None:
    """Recover Steam64 for data.save via constrained 16-bit key search."""
    cipher = path.read_bytes()
    if len(cipher) < 8:
        return None
    for z1 in (0x01, 0x5E, 0x9C, 0xDA):
        k0 = cipher[0] ^ 0x78
        k1 = cipher[1] ^ z1
        for k2 in range(256):
            for k3 in range(256):
                key8 = bytes((k0, k1, k2, k3)) + STEAM_HIGH_BYTES
                chunk = xor_stream(cipher[:256], key8)
                if chunk[:2] != bytes((0x78, z1)):
                    continue
                try:
                    plain = zlib.decompress(xor_stream(cipher, key8))
                except zlib.error:
                    continue
                if plain.startswith(DATA_PREAMBLE):
                    return le8_to_steam64(key8)
    return None


def quick_verify_data_key(path: Path, sid: int) -> bool:
    """Check whether a Steam64 likely matches data.save via zlib header bytes."""
    cipher = path.read_bytes()[:2]
    if len(cipher) < 2:
        return False
    head = xor_stream(cipher, steam64_to_le8(sid))
    return head in {b"x\x01", b"x^", b"x\x9c", b"x\xda"}


def decrypt_index(path: Path, sid: int) -> bytes:
    """Decrypt index.save plaintext."""
    return xor_stream(path.read_bytes(), steam64_to_le8(sid))


def decrypt_data(path: Path, sid: int) -> bytes:
    """Decrypt and decompress data.save plaintext."""
    return zlib.decompress(xor_stream(path.read_bytes(), steam64_to_le8(sid)))


def encrypt_index(path: Path, sid: int) -> bytes:
    """Encrypt index.save from plaintext sibling."""
    return xor_stream(path.read_bytes(), steam64_to_le8(sid))


def encrypt_data(path: Path, sid: int) -> bytes:
    """Encrypt data.save from plaintext sibling."""
    return xor_stream(zlib.compress(path.read_bytes(), level=4), steam64_to_le8(sid))


def find_save_containers(root: Path) -> list[Path]:
    """Return directories containing index.save and/or data.save."""
    found = []
    for dirpath, _, filenames in os.walk(root):
        if "index.save" in filenames or "data.save" in filenames:
            found.append(Path(dirpath))
    return sorted(found)


def detect_container_ids(container: Path) -> tuple[int | None, int | None]:
    """Detect index and data Steam64 ids for a container."""
    idx = detect_steam_id_from_index(container / "index.save") if (container / "index.save").exists() else None
    dat = None
    if (container / "data.save").exists():
        dat = idx if idx is not None and quick_verify_data_key(container / "data.save", idx) else brute_force_data_key(container / "data.save")
    return idx, dat


def resolve_old_sid_for_container(c_dir: Path, override_sid: int | None) -> tuple[int | None, int | None]:
    """Resolve per-container source Steam64 ids for data.save and index.save."""
    idx_path = c_dir / "index.save"
    data_path = c_dir / "data.save"
    if override_sid is not None:
        return override_sid, override_sid
    index_sid = detect_steam_id_from_index(idx_path) if idx_path.exists() else None
    if not data_path.exists():
        return None, index_sid
    if index_sid is not None and quick_verify_data_key(data_path, index_sid):
        return index_sid, index_sid
    return brute_force_data_key(data_path), index_sid


def resign_one_container(c_dir: Path, override_sid: int | None, new_sid: int, dry_run: bool, args: argparse.Namespace) -> dict:
    """Resign one save container and capture per-file failures."""
    idx_path = c_dir / "index.save"
    data_path = c_dir / "data.save"
    result = {
        "container": str(c_dir),
        "index_sid": None,
        "data_sid": None,
        "index_processed": False,
        "data_processed": False,
        "skipped": [],
        "failures": [],
    }
    detected_idx = detect_steam_id_from_index(idx_path) if idx_path.exists() else None
    if args.old_id and detected_idx not in (None, override_sid):
        out(args, f"warn: --old-id disagrees for {c_dir}", True)
    try:
        data_sid, index_sid = resolve_old_sid_for_container(c_dir, override_sid)
    except Exception as exc:
        result["failures"].append({"file": "detection", "stage": "resolve", "error": str(exc)})
        return result
    result["index_sid"] = index_sid
    result["data_sid"] = data_sid
    if idx_path.exists():
        if index_sid is None:
            result["failures"].append({"file": "index.save", "stage": "detect", "error": "could not determine index SteamID"})
        elif dry_run:
            result["index_processed"] = True
        else:
            try:
                idx_path.write_bytes(xor_stream(idx_path.read_bytes(), steam64_to_le8(index_sid ^ new_sid)))
                result["index_processed"] = True
            except Exception as exc:
                result["failures"].append({"file": "index.save", "stage": "rewrite", "error": str(exc)})
    else:
        result["skipped"].append("index.save")
    if data_path.exists():
        if data_sid is None:
            result["failures"].append({"file": "data.save", "stage": "detect", "error": "could not determine data SteamID (index detection and brute-force both failed)"})
        elif dry_run:
            result["data_processed"] = True
        else:
            try:
                plain = decrypt_data(data_path, data_sid)
            except Exception as exc:
                result["failures"].append({"file": "data.save", "stage": "decrypt", "error": str(exc)})
            else:
                try:
                    cipher_payload = zlib.compress(plain, level=4)
                except Exception as exc:
                    result["failures"].append({"file": "data.save", "stage": "recompress", "error": str(exc)})
                else:
                    try:
                        data_path.write_bytes(xor_stream(cipher_payload, steam64_to_le8(new_sid)))
                        result["data_processed"] = True
                    except Exception as exc:
                        result["failures"].append({"file": "data.save", "stage": "encrypt_write", "error": str(exc)})
    else:
        result["skipped"].append("data.save")
    if result["index_processed"] and result["data_processed"] and index_sid != data_sid and index_sid is not None and data_sid is not None:
        out(args, f"warn: {c_dir}: index.save and data.save have different source SteamIDs (idx={index_sid}, data={data_sid}); processing independently", True)
    return result


def write_bytes(path: Path, data: bytes, dry_run: bool) -> None:
    """Write bytes unless dry-run is active."""
    if not dry_run:
        path.write_bytes(data)


def looks_like_name_prefix(buf: bytes, name_pos: int, name_len: int) -> bool:
    """Return True when the 4-byte prefix matches a short name length."""
    if name_pos < 4:
        return False
    raw = int.from_bytes(buf[name_pos - 4:name_pos], "little")
    masked = raw & 0x7FFFFFFF
    return raw == name_len or masked == name_len or (masked & 0xFF) == name_len


def decode_variable_value(tail: bytes) -> object:
    """Best-effort decode of a value following a matched variable name."""
    if len(tail) >= 4:
        small = int.from_bytes(tail[:4], "little")
        if small < 0x10000:
            return small
        fval = struct.unpack("<f", tail[:4])[0]
        if fval == fval and abs(fval) < 1e9:
            return round(fval, 6)
        slen = int.from_bytes(tail[:4], "little") & 0x7FFFFFFF
        sraw = tail[4:4 + slen]
        if 0 < slen <= len(tail) - 4 and all(32 <= c < 127 for c in sraw):
            return sraw.decode("ascii", "replace")
    return "?"


def parse_variables(blob: bytes) -> dict:
    """Best-effort parse of known variable names."""
    result = {}
    for name in KNOWN_NAMES:
        needle = name.encode("ascii")
        pos = blob.find(needle)
        while pos != -1:
            if looks_like_name_prefix(blob, pos, len(needle)):
                tail = blob[pos + len(needle):pos + len(needle) + 32]
                result[name] = decode_variable_value(tail)
                break
            pos = blob.find(needle, pos + 1)
    for name in KNOWN_NAMES:
        if name not in result and name.encode("ascii") in blob:
            result[name] = "present"
    return result


def cmd_inspect(args: argparse.Namespace) -> int:
    root = Path(args.target)
    counts = {"index": 0, "data": 0, "copied": 0, "skipped": 0}
    detected = []
    for container in find_save_containers(root):
        idx, dat = detect_container_ids(container)
        counts["index"] += int((container / "index.save").exists())
        counts["data"] += int((container / "data.save").exists())
        state = "ok" if idx == dat or idx is None or dat is None else "warn"
        out(args, f"{container}: index={idx} data={dat} status={state}")
        detected.extend(v for v in (idx, dat) if v is not None)
    sid = detected[0] if len(set(detected)) == 1 and detected else "N/A"
    summary(args, {"command": "inspect", "status": "ok", "source_steam64": sid, "target_steam64": "N/A", "files": counts})
    return EXIT_OK


def cmd_decrypt(args: argparse.Namespace) -> int:
    root = Path(args.src)
    counts = {"index": 0, "data": 0, "copied": 0, "skipped": 0}
    source_sid = None
    for container in find_save_containers(root):
        idx_sid, dat_sid = detect_container_ids(container)
        sid = parse_steam_id(args.steam_id) if args.steam_id else (idx_sid or dat_sid)
        if sid is None:
            counts["skipped"] += 1
            out(args, f"warn: could not detect steam id for {container}", True)
            continue
        source_sid = source_sid or sid
        if (container / "index.save").exists():
            write_bytes(container / "index.save.decrypted", decrypt_index(container / "index.save", sid), False)
            counts["index"] += 1
        if (container / "data.save").exists():
            write_bytes(container / "data.save.decrypted", decrypt_data(container / "data.save", sid), False)
            counts["data"] += 1
    summary(args, {"command": "decrypt", "status": "ok", "source_steam64": source_sid or "N/A", "target_steam64": "N/A", "files": counts})
    return EXIT_OK


def cmd_encrypt(args: argparse.Namespace) -> int:
    root = Path(args.src)
    sid = parse_steam_id(args.steam_id)
    counts = {"index": 0, "data": 0, "copied": 0, "skipped": 0}
    for path in root.rglob("*.decrypted"):
        if path.name == "index.save.decrypted":
            write_bytes(path.with_name("index.save"), encrypt_index(path, sid), False)
            counts["index"] += 1
        elif path.name == "data.save.decrypted":
            write_bytes(path.with_name("data.save"), encrypt_data(path, sid), False)
            counts["data"] += 1
    summary(args, {"command": "encrypt", "status": "ok", "source_steam64": sid, "target_steam64": sid, "files": counts})
    return EXIT_OK


def validate_resign(src: Path, dst: Path, yes: bool) -> None:
    """Validate resign source and destination safety rules."""
    src_r, dst_r = src.resolve(), dst.resolve()
    if src_r == dst_r:
        raise fail("src and dst are the same", EXIT_SAFETY)
    if str(dst_r).startswith(str(src_r) + os.sep):
        raise fail("dst is inside src", EXIT_SAFETY)
    if str(src_r).startswith(str(dst_r) + os.sep):
        raise fail("src is inside dst", EXIT_SAFETY)
    if dst.exists() and dst.is_file():
        raise fail(f"--dst points to a file, not a directory: {dst}", EXIT_SAFETY)
    if dst.exists() and any(dst.iterdir()) and not yes:
        raise fail("dst exists and is not empty; pass --yes", EXIT_SAFETY)


def cmd_resign(args: argparse.Namespace) -> int:
    src, dst = Path(args.src), Path(args.dst)
    validate_resign(src, dst, args.yes)
    if args.dry_run:
        out(args, f"would copy {src} -> {dst}")
    else:
        shutil.copytree(src, dst, dirs_exist_ok=args.yes)
    new_sid = parse_steam_id(args.new_id)
    override_sid = parse_steam_id(args.old_id) if args.old_id else None
    counts = {"index": 0, "data": 0, "copied": 0, "skipped": 0}
    counts["copied"] = sum(1 for _ in src.rglob("*") if _.is_file())

    results = []
    old_seen = []
    container_root = src if args.dry_run else dst
    for container in find_save_containers(container_root):
        res = resign_one_container(container, override_sid, new_sid, args.dry_run, args)
        results.append(res)
        if res["index_processed"]:
            counts["index"] += 1
        if res["data_processed"]:
            counts["data"] += 1
        counts["skipped"] += len(res["skipped"])
        if res["data_sid"] is not None:
            old_seen.append(res["data_sid"])
        elif res["index_sid"] is not None:
            old_seen.append(res["index_sid"])

    containers_total = len(results)
    containers_with_failures = sum(1 for r in results if r["failures"])
    containers_fully_failed = sum(1 for r in results if r["failures"] and not r["index_processed"] and not r["data_processed"])
    total_processed = counts["index"] + counts["data"]

    partial_failures = []
    for r in results:
        for f in r["failures"]:
            partial_failures.append({
                "container": r["container"],
                "file": f["file"],
                "stage": f["stage"],
                "error": f["error"],
            })

    source_sid = old_seen[0] if len(set(old_seen)) == 1 and old_seen else "N/A"
    status = "ok"
    exit_code = EXIT_OK
    if containers_total == 0:
        pass
    elif total_processed == 0:
        status = "error"
        exit_code = EXIT_IO
    summary_payload = {
        "command": "resign",
        "status": status,
        "source_steam64": source_sid,
        "target_steam64": new_sid,
        "files": counts,
    }
    if partial_failures:
        summary_payload["partial_failures"] = partial_failures
    summary(args, summary_payload)
    return exit_code


def cmd_bruteforce(args: argparse.Namespace) -> int:
    path = Path(args.path)
    sid = detect_steam_id_from_index(path) if path.name == "index.save" else brute_force_data_key(path)
    if sid is None:
        raise fail(f"could not recover steam64 for {path}")
    out(args, f"{path}: steam64={sid}")
    summary(args, {"command": "bruteforce", "status": "ok", "source_steam64": sid, "target_steam64": "N/A", "files": {"index": int(path.name == 'index.save'), "data": int(path.name == 'data.save'), "copied": 0, "skipped": 0}})
    return EXIT_OK


def cmd_parse(args: argparse.Namespace) -> int:
    path = Path(args.path)
    sid = detect_steam_id_from_index(path) if path.name == "index.save" else brute_force_data_key(path)
    if sid is None:
        raise fail(f"could not detect steam64 for {path}")
    plain = decrypt_index(path, sid) if path.name == "index.save" else decrypt_data(path, sid)
    variables = parse_variables(plain)
    base = path.with_suffix("")
    report = base.parent / f"{base.name}_report.txt"
    vjson = base.parent / f"{base.name}_variables.json"
    report.write_text("\n".join(f"{k}: {v}" for k, v in variables.items()) + "\n", encoding="utf-8")
    vjson.write_text(json.dumps(variables, indent=2, sort_keys=True), encoding="utf-8")
    for key, value in variables.items():
        out(args, f"{key}: {value}")
    summary(args, {"command": "parse", "status": "ok", "source_steam64": sid, "target_steam64": "N/A", "files": {"index": int(path.name == 'index.save'), "data": int(path.name == 'data.save'), "copied": 2, "skipped": 0}})
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(prog="save_resign.py")
    parser.add_argument("--json", action="store_true", help="emit final-line JSON summary")
    parser.add_argument("--version", action="version", version=f"save_resign.py {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect"); p.add_argument("target"); p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("decrypt"); p.add_argument("--src", required=True); p.add_argument("--steam-id"); p.set_defaults(func=cmd_decrypt)
    p = sub.add_parser("encrypt"); p.add_argument("--src", required=True); p.add_argument("--steam-id", required=True); p.set_defaults(func=cmd_encrypt)
    p = sub.add_parser("resign"); p.add_argument("--src", required=True); p.add_argument("--dst", required=True); p.add_argument("--new-id", required=True); p.add_argument("--old-id"); p.add_argument("--yes", action="store_true"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(func=cmd_resign)
    p = sub.add_parser("bruteforce"); p.add_argument("path"); p.set_defaults(func=cmd_bruteforce)
    p = sub.add_parser("parse"); p.add_argument("path"); p.set_defaults(func=cmd_parse)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        code_str, _, msg = str(exc).partition(":")
        code = int(code_str) if code_str.isdigit() else EXIT_IO
        if args.json:
            print(json.dumps({"status": "error", "error": msg or str(exc), "command": getattr(args, 'command', None)}))
        else:
            print(f"error: {msg or exc}", file=sys.stderr)
        return code
    except OSError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc), "command": getattr(args, 'command', None)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_IO
    except Exception as exc:
        if getattr(args, 'json', False):
            print(json.dumps({
                "status": "error",
                "error": f"unhandled: {type(exc).__name__}: {exc}",
                "command": getattr(args, 'command', None),
            }))
        else:
            print(f"error: unhandled {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
