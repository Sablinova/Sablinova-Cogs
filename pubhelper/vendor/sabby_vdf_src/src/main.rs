// sabby_vdf — generate Steam-format `remotecache.vdf` from a remote/ folder.
//
// Replicates the output of mi5hmash/RemoteCacheVdfGenerator (MIT) without
// requiring a .NET runtime and without any interactive exit prompt.
//
// Output format (mirrors upstream `RemoteCacheVdfFile.ExportAsKvGroup` and
// `KvSerializer.Serialize`):
//
// "<AppId>"
// {
//     "ChangeNumber"  "0"
//     "OSType" "0"
//     "<relative path>"
//     {
//         "root"  "0"
//         "size"  "<bytes>"
//         "localtime"     "<unix seconds>"
//         "time"  "<unix seconds>"
//         "remotetime"    "0"
//         "sha"   "<lowercase sha1 hex>"
//         "syncstate"     "0"
//         "persiststate"  "0"
//         "platformstosync2"      "-1"
//     }
//     ...
// }
//
// Indentation is tabs; one tab per level. Key/value separator is two tabs.
// Lines end with a single LF. Output is UTF-8 (no BOM).

use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn usage(stream: &mut dyn Write, prog: &str) {
    let _ = writeln!(
        stream,
        "sabby_vdf {ver}\n\
         Generate Steam `remotecache.vdf` for a save remote/ folder.\n\
         \n\
         Usage:\n\
           {prog} --app-id <id> --remote <path-to-remote-folder> [--out <path>]\n\
           {prog} -a <id> -p <path-to-remote-folder> [-o <path>]\n\
         \n\
         Options:\n\
           -a, --app-id <id>     Steam application id (integer).\n\
           -p, --remote <path>   Path to the `remote` folder.\n\
           -o, --out <path>      Output file path. Default: ./remotecache.vdf\n\
           -h, --help            Show this help.\n\
           -V, --version         Print version.\n\
         \n\
         Exit codes: 0 ok, 2 bad args, 3 io error.",
        ver = VERSION,
        prog = prog
    );
}

#[derive(Default)]
struct Args {
    app_id: Option<i64>,
    remote: Option<PathBuf>,
    out: Option<PathBuf>,
    help: bool,
    version: bool,
}

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args::default();
    let mut i = 1;
    while i < argv.len() {
        let arg = &argv[i];
        match arg.as_str() {
            "-h" | "--help" => a.help = true,
            "-V" | "--version" => a.version = true,
            "-a" | "--app-id" => {
                i += 1;
                let v = argv
                    .get(i)
                    .ok_or_else(|| format!("{arg} requires a value"))?;
                let n: i64 = v
                    .parse()
                    .map_err(|_| format!("invalid integer for {arg}: {v}"))?;
                a.app_id = Some(n);
            }
            "-p" | "--remote" => {
                i += 1;
                let v = argv
                    .get(i)
                    .ok_or_else(|| format!("{arg} requires a value"))?;
                a.remote = Some(PathBuf::from(v));
            }
            "-o" | "--out" => {
                i += 1;
                let v = argv
                    .get(i)
                    .ok_or_else(|| format!("{arg} requires a value"))?;
                a.out = Some(PathBuf::from(v));
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        i += 1;
    }
    Ok(a)
}

fn now_unix_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

// Minimal SHA-1 in pure Rust (no deps). RFC 3174.
fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [
        0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0,
    ];
    let bit_len = (data.len() as u64).wrapping_mul(8);

    // Pad: append 0x80, then zeros, then 64-bit big-endian length.
    let mut msg = Vec::with_capacity(data.len() + 72);
    msg.extend_from_slice(data);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());

    for chunk in msg.chunks_exact(64) {
        let mut w = [0u32; 80];
        for (i, word) in chunk.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A827999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1u32),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDCu32),
                _ => (b ^ c ^ d, 0xCA62C1D6u32),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    let mut out = [0u8; 20];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

struct CachedFile {
    relative_path: String,
    size: u64,
    local_time: i64,
    time: i64,
    remote_time: i64,
    sha: String,
    root: i32,
    sync_state: i32,
    persist_state: i32,
    platforms_to_sync2: i32,
}

fn walk(root: &Path) -> io::Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            let ft = entry.file_type()?;
            let p = entry.path();
            if ft.is_dir() {
                stack.push(p);
            } else if ft.is_file() {
                out.push(p);
            } else if ft.is_symlink() {
                // Resolve symlinks for files; reject symlinks pointing outside root.
                let target = fs::canonicalize(&p)?;
                let canon_root = fs::canonicalize(root)?;
                if !target.starts_with(&canon_root) {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!("symlink escapes root: {}", p.display()),
                    ));
                }
                if target.is_file() {
                    out.push(p);
                }
            }
        }
    }
    // Deterministic ordering for reproducibility.
    out.sort();
    Ok(out)
}

fn to_relative_forward_slash(path: &Path, root: &Path) -> io::Result<String> {
    let rel = path.strip_prefix(root).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("file {} is not under root {}", path.display(), root.display()),
        )
    })?;
    let mut s = String::new();
    for (i, comp) in rel.components().enumerate() {
        if i > 0 {
            s.push('/');
        }
        s.push_str(comp.as_os_str().to_str().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "non-UTF-8 path component",
            )
        })?);
    }
    Ok(s)
}

fn build_cached_files(root: &Path) -> io::Result<Vec<CachedFile>> {
    let mut out = Vec::new();
    let files = walk(root)?;
    let now = now_unix_seconds();
    for f in files {
        let data = fs::read(&f)?;
        let size = data.len() as u64;
        let sha = hex_lower(&sha1(&data));
        let rel = to_relative_forward_slash(&f, root)?;
        out.push(CachedFile {
            relative_path: rel,
            size,
            local_time: now,
            time: now,
            remote_time: 0,
            sha,
            root: 0,
            sync_state: 0,
            persist_state: 0,
            platforms_to_sync2: -1,
        });
    }
    Ok(out)
}

fn serialize(app_id: i64, files: &[CachedFile]) -> String {
    // Mirror upstream KvSerializer.SerializeGroup formatting exactly:
    // - tab indentation per level
    // - key line: <pad>"<key>"\n
    // - open brace: <pad>{\n
    // - pair line: <pad>\t"<key>"\t\t"<value>"\n
    // - close brace: <pad>}\n
    let mut s = String::new();
    s.push_str(&format!("\"{}\"\n", app_id));
    s.push_str("{\n");
    s.push_str(&format!("\t\"ChangeNumber\"\t\t\"{}\"\n", 0));
    s.push_str(&format!("\t\"OSType\"\t\t\"{}\"\n", 0));
    for f in files {
        s.push_str(&format!("\t\"{}\"\n", f.relative_path));
        s.push_str("\t{\n");
        s.push_str(&format!("\t\t\"root\"\t\t\"{}\"\n", f.root));
        s.push_str(&format!("\t\t\"size\"\t\t\"{}\"\n", f.size));
        s.push_str(&format!("\t\t\"localtime\"\t\t\"{}\"\n", f.local_time));
        s.push_str(&format!("\t\t\"time\"\t\t\"{}\"\n", f.time));
        s.push_str(&format!("\t\t\"remotetime\"\t\t\"{}\"\n", f.remote_time));
        s.push_str(&format!("\t\t\"sha\"\t\t\"{}\"\n", f.sha));
        s.push_str(&format!("\t\t\"syncstate\"\t\t\"{}\"\n", f.sync_state));
        s.push_str(&format!("\t\t\"persiststate\"\t\t\"{}\"\n", f.persist_state));
        s.push_str(&format!(
            "\t\t\"platformstosync2\"\t\t\"{}\"\n",
            f.platforms_to_sync2
        ));
        s.push_str("\t}\n");
    }
    s.push_str("}\n");
    s
}

fn main() -> ExitCode {
    let argv: Vec<String> = env::args().collect();
    let prog = argv
        .get(0)
        .map(|s| Path::new(s).file_name().unwrap_or(OsStr::new("sabby_vdf")).to_string_lossy().into_owned())
        .unwrap_or_else(|| "sabby_vdf".to_string());

    let args = match parse_args(&argv) {
        Ok(a) => a,
        Err(e) => {
            let _ = writeln!(io::stderr(), "error: {}", e);
            usage(&mut io::stderr(), &prog);
            return ExitCode::from(2);
        }
    };

    if args.help {
        usage(&mut io::stdout(), &prog);
        return ExitCode::SUCCESS;
    }
    if args.version {
        println!("sabby_vdf {}", VERSION);
        return ExitCode::SUCCESS;
    }

    let app_id = match args.app_id {
        Some(a) => a,
        None => {
            let _ = writeln!(io::stderr(), "error: --app-id is required");
            usage(&mut io::stderr(), &prog);
            return ExitCode::from(2);
        }
    };
    let remote = match args.remote {
        Some(r) => r,
        None => {
            let _ = writeln!(io::stderr(), "error: --remote is required");
            usage(&mut io::stderr(), &prog);
            return ExitCode::from(2);
        }
    };
    let out = args
        .out
        .unwrap_or_else(|| PathBuf::from("remotecache.vdf"));

    if !remote.is_dir() {
        let _ = writeln!(
            io::stderr(),
            "error: remote folder not found or not a directory: {}",
            remote.display()
        );
        return ExitCode::from(3);
    }

    let files = match build_cached_files(&remote) {
        Ok(v) => v,
        Err(e) => {
            let _ = writeln!(io::stderr(), "error: failed to scan remote folder: {}", e);
            return ExitCode::from(3);
        }
    };

    let serialized = serialize(app_id, &files);

    if let Err(e) = fs::write(&out, serialized.as_bytes()) {
        let _ = writeln!(io::stderr(), "error: failed to write {}: {}", out.display(), e);
        return ExitCode::from(3);
    }

    eprintln!(
        "sabby_vdf: wrote {} ({} bytes, {} files, app-id {})",
        out.display(),
        serialized.len(),
        files.len(),
        app_id
    );
    ExitCode::SUCCESS
}
