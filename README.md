# Mini-UnionFS — Complete Project

A fully working userspace Union File System built with **FUSE**, plus a **Python/Tkinter GUI** to explore it interactively.

---

## What Is This?

Mini-UnionFS stacks two directories — a **read-only lower layer** and a **read-write upper layer** — and presents them as a single unified mount point. This is the same mechanism Docker uses for container image layers.

| Feature | Description |
|---|---|
| **Layer stacking** | Files in upper take precedence over lower |
| **Copy-on-Write (CoW)** | Writing to a lower-layer file copies it to upper first |
| **Whiteout files** | Deleting a lower-layer file creates `.wh.<name>` in upper |
| **Whiteout hiding** | Whiteout files are invisible in the union view |
| **Full POSIX ops** | `getattr`, `readdir`, `read`, `write`, `create`, `unlink`, `mkdir`, `rmdir`, `rename`, `truncate`, `chmod`, `utimens` |

---

## File Layout

```
mini_unionfs/
├── mini_unionfs.c      ← FUSE backend (C)
├── Makefile            ← build system
├── unionfs_gui.py      ← GUI application (Python 3 + Tkinter)
├── test_unionfs.sh     ← automated test suite
└── README.md           ← this file
```

---

## Prerequisites

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y build-essential libfuse3-dev fuse3 pkg-config python3-tk
```

> **FUSE user mounts** — Ensure your user is in the `fuse` group or that `/etc/fuse.conf` contains `user_allow_other`. Usually not needed for basic mounts.

---

## Build

```bash
cd mini_unionfs
make
```

You should see a `mini_unionfs` binary produced, and `test_unionfs.sh` will be marked executable automatically.

```bash
ls -lh mini_unionfs
# -rwxr-xr-x  ... mini_unionfs
```

---

## Running the GUI

```bash
python3 unionfs_gui.py
```

### Quick-start inside the GUI

1. **File → Quick Setup** — picks a base directory, creates `lower/`, `upper/`, `mnt/` subdirectories, and seeds `lower/` with demo files automatically.
2. Click **⬡ Mount** — the FUSE process starts and the three-pane view populates.
3. Use the action buttons on the right to exercise POSIX operations.
4. Click **⬡ Unmount** when done.

### GUI Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Toolbar: [lower_dir] [upper_dir] [mount_dir]  [Mount] [Refresh] │
├─────────────────────────┬────────────────────────────────────────┤
│  LOWER   UNION   UPPER  │  Action buttons                        │
│  (read   (merged (read  │  ─────────────                        │
│   only)   view)  write) │  📄 Create File   📁 Create Dir        │
│                         │  ✏️  Write/Append  👁️  Read File         │
│  Double-click a dir     │  🚮 Delete                             │
│  in UNION to enter it.  │  📋 Copy In                           │
│  [Up] to go back.       │  ─────────────                        │
│                         │  🔍 Show Whiteouts                    │
│  🔵 = lower-only        │  🧅 Layer Stack View                  │
│  🟢 = upper / CoW       │  🧪 Run Test Suite                    │
│  🔴 = whiteout (hidden) │                                        │
│                         │  [ log console ]                       │
└─────────────────────────┴────────────────────────────────────────┘
│  Status bar: mount paths + mount indicator                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Running the CLI Binary Directly

```bash
# Create directories
mkdir -p /tmp/test/{lower,upper,mnt}

# Seed lower layer
echo "hello from lower" > /tmp/test/lower/hello.txt
echo "will be deleted"  > /tmp/test/lower/bye.txt

# Mount (runs in background)
./mini_unionfs /tmp/test/lower /tmp/test/upper /tmp/test/mnt &

# Verify union shows lower files
ls /tmp/test/mnt
cat /tmp/test/mnt/hello.txt

# Unmount
fusermount -u /tmp/test/mnt
```

---

## Manual Verification Scenarios

Work through each scenario to confirm correct behaviour.

### Scenario 1 — Layer Visibility

```bash
mkdir -p /tmp/ufs/{lower,upper,mnt}
echo "lower content" > /tmp/ufs/lower/file.txt
./mini_unionfs /tmp/ufs/lower /tmp/ufs/upper /tmp/ufs/mnt &
sleep 0.5

cat /tmp/ufs/mnt/file.txt
# Expected: "lower content"

ls /tmp/ufs/mnt
# Expected: file.txt listed
```

### Scenario 2 — Copy-on-Write

```bash
echo "appended" >> /tmp/ufs/mnt/file.txt

# Union shows new content
cat /tmp/ufs/mnt/file.txt
# Expected: "lower content\nappended"

# Upper has a copy
cat /tmp/ufs/upper/file.txt
# Expected: "lower content\nappended"

# Lower is untouched
cat /tmp/ufs/lower/file.txt
# Expected: "lower content" (no appended line)
```

### Scenario 3 — Whiteout (Deletion)

```bash
echo "delete me" > /tmp/ufs/lower/gone.txt
sleep 0.2   # give FUSE a moment to see the new lower file

rm /tmp/ufs/mnt/gone.txt

# File is invisible in union
ls /tmp/ufs/mnt/
# Expected: gone.txt NOT listed

# Lower still has the original
ls /tmp/ufs/lower/
# Expected: gone.txt still present

# Upper has the whiteout marker
ls -la /tmp/ufs/upper/
# Expected: .wh.gone.txt present (size 0)
```

### Scenario 4 — Whiteout Hides Lower File

```bash
# If you list the union it must NOT show gone.txt
ls /tmp/ufs/mnt | grep gone
# Expected: no output (empty)
```

### Scenario 5 — Creating New Files

```bash
echo "brand new" > /tmp/ufs/mnt/new.txt

ls /tmp/ufs/upper/
# Expected: new.txt present

ls /tmp/ufs/lower/
# Expected: new.txt NOT present

cat /tmp/ufs/mnt/new.txt
# Expected: "brand new"
```

### Scenario 6 — Upper Shadows Lower

```bash
echo "upper wins" > /tmp/ufs/upper/file.txt

cat /tmp/ufs/mnt/file.txt
# Expected: "upper wins" (not the lower version)
```

### Scenario 7 — mkdir & rmdir

```bash
mkdir /tmp/ufs/mnt/newdir
ls /tmp/ufs/upper/
# Expected: newdir present in upper

rmdir /tmp/ufs/mnt/newdir
ls /tmp/ufs/mnt/
# Expected: newdir gone from union
```

### Scenario 8 — Rename

```bash
mv /tmp/ufs/mnt/new.txt /tmp/ufs/mnt/renamed.txt
ls /tmp/ufs/mnt/
# Expected: renamed.txt present, new.txt gone
```

### Cleanup

```bash
fusermount -u /tmp/ufs/mnt
rm -rf /tmp/ufs
```

---

## Automated Test Suite

```bash
make test
# or equivalently:
bash test_unionfs.sh
```

> **Permission denied?** If the script was extracted without execute permissions, run it
> with `bash test_unionfs.sh` or run `make` first — the Makefile's `all` target calls
> `chmod +x test_unionfs.sh` automatically.

### What the suite covers

The suite mounts a fresh FUSE instance, runs **10 test sections** (18 assertions total),
then tears everything down. Here is exactly what each section checks:

| Section | Assertion(s) | What is verified |
|---|---|---|
| **Test 1 — Layer Visibility** (1 check) | lower file readable via mount | Files seeded into `lower/` appear in the union view |
| **Test 2 — Copy-on-Write** (3 checks) | append visible in mount · copy landed in `upper/` · `lower/` untouched | Writes to a lower-layer file trigger CoW: the modified copy goes to `upper/`, the original in `lower/` is never changed |
| **Test 3 — Whiteout Mechanism** (4 checks) | file gone from mount · original intact in `lower/` · `.wh.<name>` exists in `upper/` | `rm` on a lower-layer file creates a whiteout marker instead of deleting the source |
| **Test 4 — Create New Files** (3 checks) | file visible in mount · file in `upper/` · file absent from `lower/` | New files created via the union point land in `upper/` only |
| **Test 5 — Upper Shadows Lower** (1 check) | mount returns upper version | When the same filename exists in both layers, the `upper/` copy wins |
| **Test 6 — Subdirectory Visibility** (2 checks) | subdir present · file inside it readable | Nested directories and their contents are merged correctly |
| **Test 7 — mkdir in Union** (2 checks) | new dir visible in mount · dir in `upper/` | Creating a directory via the mount point creates it in `upper/` |
| **Test 8 — Rename** (2 checks) | new name present · old name gone | Renaming a file through the mount point works end-to-end |
| **Test 9 — Overwrite upper file** (1 check) | overwritten content readable | Writing to a file already in `upper/` updates in place |
| **Test 10 — Re-create whiteouted file** (2 checks) | resurrected content readable · whiteout marker removed | Re-creating a previously deleted lower-layer file clears its whiteout |

### Expected output

```
  ╔═══════════════════════════════════════╗
  ║    Mini-UnionFS Test Suite            ║
  ╚═══════════════════════════════════════╝

── Test 1: Layer Visibility ──
  lower file visible in union... PASSED

── Test 2: Copy-on-Write ──
  append to lower file... PASSED
  upper has modified copy... PASSED
  lower untouched... PASSED

── Test 3: Whiteout Mechanism ──
  delete lower file via mount... 
  file hidden from union... PASSED
  original still in lower... PASSED
  whiteout created in upper... PASSED

── Test 4: Create New Files ──
  create file in union... PASSED
  new file lands in upper... PASSED
  lower unchanged... PASSED

── Test 5: Upper Shadows Lower ──
  create upper shadow of lower file... PASSED

── Test 6: Subdirectory Visibility ──
  subdir visible in union... PASSED
  file inside subdir visible... PASSED

── Test 7: mkdir in Union ──
  create directory in union... PASSED
  directory appears in upper... PASSED

── Test 8: Rename ──
  rename file in union... PASSED
  original name gone... PASSED

── Test 9: Overwrite upper file ──
  overwrite upper-only file... PASSED

── Test 10: Re-create whiteouted file ──
  re-create previously deleted file... PASSED
  whiteout cleared after re-create... PASSED

  ╔══════════════════════════════════╗
  ║  Results: 18 passed   0 failed  ║
  ╚══════════════════════════════════╝
```

---

## Architecture / Design Notes

### State

```c
struct unionfs_state {
    char *lower_dir;   // read-only base layer
    char *upper_dir;   // read-write upper layer
};
```

Passed to FUSE via `fuse_main` and retrieved in every callback with `fuse_get_context()->private_data`.

### Path Resolution (`resolve_path`)

1. Check if `upper_dir + "/.wh." + name` exists → return `ENOENT` (hidden by whiteout)
2. Check if `upper_dir + path` exists → return it (upper takes precedence)
3. Check if `lower_dir + path` exists → return it
4. Return `ENOENT`

### Copy-on-Write (`cow_copy`)

Triggered in `u_open` when the flags include write access and the file doesn't yet exist in `upper_dir`:

1. `open(lower_file, O_RDONLY)`
2. `mkdir_parents(upper_path)` — recreate directory structure
3. `open(upper_file, O_WRONLY|O_CREAT|O_TRUNC, original_mode)`
4. Stream copy in 64 KB chunks
5. Continue the write on the upper copy

### Whiteout (`u_unlink`)

- If the file exists in `upper_dir` → `unlink(upper_file)`, then if also in `lower_dir`, create `.wh.<name>` in upper.
- If the file exists only in `lower_dir` → create `upper_dir/.wh.<name>` (empty, mode 0000).

### `readdir` Merge

- Scan `upper_dir` first; record all names except `.wh.*` files.
- Scan `lower_dir`; skip names for which a whiteout exists in upper, and skip names already seen from upper.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `fuse: device not found` | `sudo modprobe fuse` |
| `Transport endpoint not connected` | `fusermount -u <mount>` to force unmount |
| GUI can't find binary | Run `make` first; keep GUI in the same directory |
| `pkg-config: fuse3 not found` | Install `libfuse3-dev` (Debian) or `fuse3-devel` (Fedora) |
| Permission denied on mount | Add yourself to the fuse group: `sudo usermod -aG fuse $USER`, then log out/in |
| Permission denied on `test_unionfs.sh` | Run `bash test_unionfs.sh`, or `make` (which sets the execute bit automatically) |

---

## Limitations (by design for this mini version)

- No hard-link support across layers
- No `xattr` support
- No concurrent-writer safety (single-threaded FUSE)
- Whiteouts for directories use the same `.wh.` convention as files

---

## License

MIT — free to use, modify, and distribute.
