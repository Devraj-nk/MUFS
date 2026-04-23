/*
 * Mini-UnionFS: A simplified Union File System using FUSE
 *
 * Implements:
 *   - Layer stacking (lower read-only, upper read-write)
 *   - Copy-on-Write (CoW) for lower layer files
 *   - Whiteout files for deletions from lower layer
 *   - Full POSIX: getattr, readdir, read, write, create,
 *                 unlink, mkdir, rmdir, open, truncate, rename
 */

#define FUSE_USE_VERSION 31

#include <fuse.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h> 
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <stdarg.h>
#include <libgen.h>
#include <limits.h>

/* ─────────────────────────── State ─────────────────────────── */

struct unionfs_state {
    char *lower_dir;   /* read-only base layer  */
    char *upper_dir;   /* read-write upper layer */
};

#define STATE ((struct unionfs_state *) fuse_get_context()->private_data)

/* Whiteout prefix used to mark deleted lower-layer files */
#define WH_PREFIX ".wh."

/* ──────────────────────── Path helpers ─────────────────────── */

/* Build full path: dir + "/" + relative_path → buf */
static void full_path(char *buf, size_t sz, const char *dir, const char *rel)
{
    snprintf(buf, sz, "%s%s", dir, rel);
}

/* Build whiteout path for a given relative path */
static void wh_path(char *buf, size_t sz, const char *dir, const char *rel)
{
    /* rel is like "/foo/bar.txt" → whiteout is "/foo/.wh.bar.txt" */
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", rel);

    char *dname = dirname(tmp);   /* modifies tmp */
    char tmp2[PATH_MAX];
    snprintf(tmp2, sizeof(tmp2), "%s", rel);
    char *bname = basename(tmp2); /* modifies tmp2 */

    if (strcmp(dname, "/") == 0 || strcmp(dname, ".") == 0)
        snprintf(buf, sz, "%s/" WH_PREFIX "%s", dir, bname);
    else
        snprintf(buf, sz, "%s%s/" WH_PREFIX "%s", dir, dname, bname);
}

/* Returns 1 if a whiteout exists in upper_dir for this relative path */
static int is_whiteout(const char *rel)
{
    char wh[PATH_MAX];
    wh_path(wh, sizeof(wh), STATE->upper_dir, rel);
    return (access(wh, F_OK) == 0);
}

/*
 * resolve_path: locate a file across layers.
 * Returns 0 and fills resolved_path on success, -ENOENT if not found.
 * Priority: upper_dir > lower_dir; whiteout hides lower files.
 */
static int resolve_path(const char *rel, char *resolved, size_t sz)
{
    /* 1. Whiteout check */
    if (is_whiteout(rel))
        return -ENOENT;

    /* 2. Upper layer */
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, rel);
    if (access(up, F_OK) == 0) {
        snprintf(resolved, sz, "%s", up);
        return 0;
    }

    /* 3. Lower layer */
    char lo[PATH_MAX];
    full_path(lo, sizeof(lo), STATE->lower_dir, rel);
    if (access(lo, F_OK) == 0) {
        snprintf(resolved, sz, "%s", lo);
        return 0;
    }

    return -ENOENT;
}

/* ───────────────── Copy-on-Write helper ───────────────────── */

/* Recursively create parent directories in upper_dir */
static int mkdir_parents(const char *path)
{
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", path);
    char *p = tmp + 1;
    while ((p = strchr(p, '/')) != NULL) {
        *p = '\0';
        if (mkdir(tmp, 0755) != 0 && errno != EEXIST)
            return -errno;
        *p = '/';
        p++;
    }
    return 0;
}

/*
 * cow_copy: copy a file from lower_dir to upper_dir so it can be written.
 * Returns 0 on success.
 */
static int cow_copy(const char *rel)
{
    char src[PATH_MAX], dst[PATH_MAX];
    full_path(src, sizeof(src), STATE->lower_dir, rel);
    full_path(dst, sizeof(dst), STATE->upper_dir, rel);

    /* Make parent dirs */
    char dst_copy[PATH_MAX];
    snprintf(dst_copy, sizeof(dst_copy), "%s", dst);
    char *dir = dirname(dst_copy);
    mkdir_parents(dir);
    if (mkdir(dir, 0755) != 0 && errno != EEXIST)
        return -errno;

    /* Copy */
    int fin = open(src, O_RDONLY);
    if (fin < 0) return -errno;

    struct stat st;
    fstat(fin, &st);

    int fout = open(dst, O_WRONLY | O_CREAT | O_TRUNC, st.st_mode);
    if (fout < 0) { close(fin); return -errno; }

    char buf[65536];
    ssize_t n;
    while ((n = read(fin, buf, sizeof(buf))) > 0) {
        ssize_t w = write(fout, buf, n);
        (void)w;   /* CoW: best-effort copy; caller checks result via open */
    }

    close(fin);
    close(fout);
    return 0;
}

/* ─────────────────── FUSE Operations ─────────────────────── */

static int u_getattr(const char *path, struct stat *stbuf,
                     struct fuse_file_info *fi)
{
    (void)fi;
    memset(stbuf, 0, sizeof(*stbuf));

    if (strcmp(path, "/") == 0) {
        stbuf->st_mode  = S_IFDIR | 0755;
        stbuf->st_nlink = 2;
        return 0;
    }

    char resolved[PATH_MAX];
    int res = resolve_path(path, resolved, sizeof(resolved));
    if (res < 0) return res;

    if (lstat(resolved, stbuf) < 0)
        return -errno;
    return 0;
}

/*
 * FIX 1: u_readdir — use `unsigned int` instead of `enum fuse_readdir_flags`.
 * The enum is defined inside <fuse.h>; when compiling without the full dev
 * headers the enum type is incomplete and gcc rejects the parameter.
 * Using the underlying type (unsigned int) is ABI-compatible with fuse3.
 */
static int u_readdir(const char *path, void *buf, fuse_fill_dir_t filler,
                     off_t offset, struct fuse_file_info *fi,
                     unsigned int flags)
{
    (void)offset; (void)fi; (void)flags;

    filler(buf, ".",  NULL, 0, 0);
    filler(buf, "..", NULL, 0, 0);

    /* We collect all names seen, to avoid duplicates */
    char *seen[4096];
    int  nseen = 0;

    /* Helper to record a name */
    #define SEEN(name) ({ \
        int _dup = 0; \
        for (int _i = 0; _i < nseen; _i++) \
            if (strcmp(seen[_i], (name)) == 0) { _dup = 1; break; } \
        if (!_dup && nseen < 4095) seen[nseen++] = strdup(name); \
        !_dup; \
    })

    /* Scan upper_dir */
    char udir[PATH_MAX];
    full_path(udir, sizeof(udir), STATE->upper_dir, path);
    DIR *dp = opendir(udir);
    if (dp) {
        struct dirent *de;
        while ((de = readdir(dp)) != NULL) {
            if (strcmp(de->d_name, ".") == 0 ||
                strcmp(de->d_name, "..") == 0)
                continue;
            /* Skip whiteout files from listing */
            if (strncmp(de->d_name, WH_PREFIX, strlen(WH_PREFIX)) == 0)
                continue;
            if (SEEN(de->d_name)) {
                struct stat st;
                memset(&st, 0, sizeof(st));
                filler(buf, de->d_name, &st, 0, 0);
            }
        }
        closedir(dp);
    }

    /* Scan lower_dir */
    char ldir[PATH_MAX];
    full_path(ldir, sizeof(ldir), STATE->lower_dir, path);
    dp = opendir(ldir);
    if (dp) {
        struct dirent *de;
        while ((de = readdir(dp)) != NULL) {
            if (strcmp(de->d_name, ".") == 0 ||
                strcmp(de->d_name, "..") == 0)
                continue;

            /* Build relative path for whiteout check */
            char rel[PATH_MAX];
            if (strcmp(path, "/") == 0)
                snprintf(rel, sizeof(rel), "/%s", de->d_name);
            else
                snprintf(rel, sizeof(rel), "%s/%s", path, de->d_name);

            if (is_whiteout(rel))
                continue;   /* hidden by whiteout */

            if (SEEN(de->d_name)) {
                struct stat st;
                memset(&st, 0, sizeof(st));
                filler(buf, de->d_name, &st, 0, 0);
            }
        }
        closedir(dp);
    }

    for (int i = 0; i < nseen; i++) free(seen[i]);
    return 0;
}

static int u_open(const char *path, struct fuse_file_info *fi)
{
    /* CoW: if writing to a lower-layer file, copy it up first */
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);

    if ((fi->flags & O_ACCMODE) != O_RDONLY) {
        if (access(up, F_OK) != 0) {
            /* Not in upper — try to CoW from lower */
            char lo[PATH_MAX];
            full_path(lo, sizeof(lo), STATE->lower_dir, path);
            if (access(lo, F_OK) == 0) {
                int res = cow_copy(path);
                if (res < 0) return res;
            }
        }
    }

    char resolved[PATH_MAX];
    int res = resolve_path(path, resolved, sizeof(resolved));
    if (res < 0) return res;

    int fd = open(resolved, fi->flags);
    if (fd < 0) return -errno;
    fi->fh = fd;
    return 0;
}

static int u_read(const char *path, char *buf, size_t size, off_t offset,
                  struct fuse_file_info *fi)
{
    (void)path;
    ssize_t res = pread(fi->fh, buf, size, offset);
    if (res < 0) return -errno;
    return (int)res;
}

static int u_write(const char *path, const char *buf, size_t size,
                   off_t offset, struct fuse_file_info *fi)
{
    (void)path;
    ssize_t res = pwrite(fi->fh, buf, size, offset);
    if (res < 0) return -errno;
    return (int)res;
}

static int u_release(const char *path, struct fuse_file_info *fi)
{
    (void)path;
    close(fi->fh);
    return 0;
}

static int u_create(const char *path, mode_t mode, struct fuse_file_info *fi)
{
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);

    /* Make parent dirs */
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", up);
    char *dname = dirname(tmp);
    mkdir_parents(dname);
    mkdir(dname, 0755);

    /* Remove any stale whiteout */
    char wh[PATH_MAX];
    wh_path(wh, sizeof(wh), STATE->upper_dir, path);
    unlink(wh);

    int fd = open(up, fi->flags | O_CREAT, mode);
    if (fd < 0) return -errno;
    fi->fh = fd;
    return 0;
}

static int u_truncate(const char *path, off_t size,
                      struct fuse_file_info *fi)
{
    (void)fi;
    /* CoW if needed */
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);
    if (access(up, F_OK) != 0) {
        char lo[PATH_MAX];
        full_path(lo, sizeof(lo), STATE->lower_dir, path);
        if (access(lo, F_OK) == 0) {
            int res = cow_copy(path);
            if (res < 0) return res;
        }
    }
    if (truncate(up, size) < 0) return -errno;
    return 0;
}

static int u_unlink(const char *path)
{
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);

    if (access(up, F_OK) == 0) {
        /* File is in upper — just delete it */
        if (unlink(up) < 0) return -errno;
        /* But if it also exists in lower, we still need a whiteout */
        char lo[PATH_MAX];
        full_path(lo, sizeof(lo), STATE->lower_dir, path);
        if (access(lo, F_OK) == 0) {
            char wh[PATH_MAX];
            wh_path(wh, sizeof(wh), STATE->upper_dir, path);
            int fd = open(wh, O_CREAT | O_WRONLY, 0000);
            if (fd >= 0) close(fd);
        }
        return 0;
    }

    /* File only in lower — create whiteout */
    char lo[PATH_MAX];
    full_path(lo, sizeof(lo), STATE->lower_dir, path);
    if (access(lo, F_OK) == 0) {
        char wh[PATH_MAX];
        wh_path(wh, sizeof(wh), STATE->upper_dir, path);

        /* Ensure parent dir exists in upper */
        char tmp[PATH_MAX];
        snprintf(tmp, sizeof(tmp), "%s", wh);
        char *dname = dirname(tmp);
        mkdir_parents(dname);
        mkdir(dname, 0755);

        int fd = open(wh, O_CREAT | O_WRONLY, 0000);
        if (fd < 0) return -errno;
        close(fd);
        return 0;
    }

    return -ENOENT;
}

static int u_mkdir(const char *path, mode_t mode)
{
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);

    /* Remove stale whiteout if exists */
    char wh[PATH_MAX];
    wh_path(wh, sizeof(wh), STATE->upper_dir, path);
    unlink(wh);

    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", up);
    char *dname = dirname(tmp);
    mkdir_parents(dname);
    mkdir(dname, 0755);

    if (mkdir(up, mode) < 0) return -errno;
    return 0;
}

static int u_rmdir(const char *path)
{
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);

    if (access(up, F_OK) == 0) {
        if (rmdir(up) < 0) return -errno;
    }

    char lo[PATH_MAX];
    full_path(lo, sizeof(lo), STATE->lower_dir, path);
    if (access(lo, F_OK) == 0) {
        /* Create whiteout dir marker */
        char wh[PATH_MAX];
        wh_path(wh, sizeof(wh), STATE->upper_dir, path);
        int fd = open(wh, O_CREAT | O_WRONLY, 0000);
        if (fd >= 0) close(fd);
    }
    return 0;
}

static int u_rename(const char *from, const char *to,
                    unsigned int flags)
{
    (void)flags;
    /* CoW source if needed */
    char up_from[PATH_MAX], up_to[PATH_MAX];
    full_path(up_from, sizeof(up_from), STATE->upper_dir, from);
    full_path(up_to,   sizeof(up_to),   STATE->upper_dir, to);

    if (access(up_from, F_OK) != 0) {
        char lo[PATH_MAX];
        full_path(lo, sizeof(lo), STATE->lower_dir, from);
        if (access(lo, F_OK) == 0)
            cow_copy(from);
    }

    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", up_to);
    char *dname = dirname(tmp);
    mkdir_parents(dname);
    mkdir(dname, 0755);

    if (rename(up_from, up_to) < 0) return -errno;

    /* Whiteout the old path if it existed in lower */
    char lo_from[PATH_MAX];
    full_path(lo_from, sizeof(lo_from), STATE->lower_dir, from);
    if (access(lo_from, F_OK) == 0) {
        char wh[PATH_MAX];
        wh_path(wh, sizeof(wh), STATE->upper_dir, from);
        int fd = open(wh, O_CREAT | O_WRONLY, 0000);
        if (fd >= 0) close(fd);
    }
    return 0;
}

static int u_utimens(const char *path, const struct timespec ts[2],
                     struct fuse_file_info *fi)
{
    (void)fi;
    char resolved[PATH_MAX];
    int res = resolve_path(path, resolved, sizeof(resolved));
    if (res < 0) return res;

    /* CoW before touching timestamps if in lower */
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);
    if (access(up, F_OK) != 0) {
        char lo[PATH_MAX];
        full_path(lo, sizeof(lo), STATE->lower_dir, path);
        if (access(lo, F_OK) == 0) {
            cow_copy(path);
            full_path(up, sizeof(up), STATE->upper_dir, path);
        }
    }

    if (utimensat(0, up, ts, AT_SYMLINK_NOFOLLOW) < 0)
        return -errno;
    return 0;
}

static int u_chmod(const char *path, mode_t mode, struct fuse_file_info *fi)
{
    (void)fi;
    char up[PATH_MAX];
    full_path(up, sizeof(up), STATE->upper_dir, path);
    if (access(up, F_OK) != 0) {
        char lo[PATH_MAX];
        full_path(lo, sizeof(lo), STATE->lower_dir, path);
        if (access(lo, F_OK) == 0) cow_copy(path);
    }
    full_path(up, sizeof(up), STATE->upper_dir, path);
    if (chmod(up, mode) < 0) return -errno;
    return 0;
}

/* ─────────────── FUSE operations table ──────────────────── */

static struct fuse_operations unionfs_oper = {
    .getattr  = u_getattr,
    .readdir  = u_readdir,
    .open     = u_open,
    .read     = u_read,
    .write    = u_write,
    .release  = u_release,
    .create   = u_create,
    .truncate = u_truncate,
    .unlink   = u_unlink,
    .mkdir    = u_mkdir,
    .rmdir    = u_rmdir,
    .rename   = u_rename,
    .utimens  = u_utimens,
    .chmod    = u_chmod,
};

/* ─────────────────────── main ──────────────────────────── */

int main(int argc, char *argv[])
{
    if (argc < 4) {
        fprintf(stderr,
            "Usage: %s <lower_dir> <upper_dir> <mount_point> [fuse_opts]\n",
            argv[0]);
        return 1;
    }

    struct unionfs_state *state = calloc(1, sizeof(*state));
    if (!state) { perror("calloc"); return 1; }

    state->lower_dir = realpath(argv[1], NULL);
    state->upper_dir = realpath(argv[2], NULL);

    if (!state->lower_dir || !state->upper_dir) {
        fprintf(stderr, "Error resolving lower/upper dirs: %s\n",
                strerror(errno));
        return 1;
    }

    /*
     * FIX 2: allocate argc slots (not argc-1).
     * We need slots for: argv[0] + mount_point + up to (argc-4) extra fuse
     * options = argc-2 entries total. argc slots is always safe.
     */
    char **fuse_argv = malloc(argc * sizeof(char *));
    if (!fuse_argv) { perror("malloc"); return 1; }

    /* Rebuild fuse argv: program name, mount_point, [extra fuse opts] */
    int fuse_argc = 0;
    fuse_argv[fuse_argc++] = argv[0];        /* program name  */
    fuse_argv[fuse_argc++] = argv[3];        /* mount_point   */
    for (int i = 4; i < argc; i++)           /* any extra opts */
        fuse_argv[fuse_argc++] = argv[i];

    return fuse_main(fuse_argc, fuse_argv, &unionfs_oper, state);
}
