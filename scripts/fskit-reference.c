/* Development diagnostic: one immutable in-memory file, no backing device. */
#define FUSE_USE_VERSION 26
#include <fuse.h>

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>

#define UNUSED __attribute__((unused))
#ifdef NATIVOL_REFERENCE_KERNEL
#define REFERENCE_PROGRAM "kernel-reference"
#define REFERENCE_BACKEND "kernel"
#define FILESYSTEM_PREFIX "Nativol-Kernel-Reference-"
#define MOUNT_PREFIX "/Volumes/Nativol-Kernel-Reference-"
#else
#define REFERENCE_PROGRAM "fskit-reference"
#define REFERENCE_BACKEND "fskit"
#define FILESYSTEM_PREFIX "Nativol-Reference-"
#define MOUNT_PREFIX "/Volumes/Nativol-Reference-"
#endif
#define FIXED_OPTIONS "backend=" REFERENCE_BACKEND ",local,ro,quiet"

static char fixture_name[sizeof("fixture-.txt") + 64];
static char fixture_path[sizeof(fixture_name) + 1];
static char fixture_content[sizeof("Nativol FSKit reference\n\n") + 64];
static size_t fixture_size;
static uid_t owner_uid;
static gid_t owner_gid;
static time_t creation_time;

static int lower_hex(const char *value, size_t length)
{
    if (strlen(value) != length) return 0;
    for (size_t i = 0; i < length; ++i) {
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f'))) return 0;
    }
    return 1;
}

static int known_path(const char *path)
{
    return strcmp(path, "/") == 0 || strcmp(path, fixture_path) == 0;
}

static int reference_getattr(const char *path, struct stat *out)
{
    memset(out, 0, sizeof(*out));
    if (!known_path(path)) return -ENOENT;
    out->st_uid = owner_uid;
    out->st_gid = owner_gid;
    out->st_atime = out->st_mtime = out->st_ctime = creation_time;
    out->st_blksize = 4096;
    if (strcmp(path, "/") == 0) {
        out->st_ino = 1;
        out->st_mode = S_IFDIR | 0500;
        out->st_nlink = 2;
    } else {
        out->st_ino = 2;
        out->st_mode = S_IFREG | 0400;
        out->st_nlink = 1;
        out->st_size = (off_t)fixture_size;
        out->st_blocks = 1;
    }
    return 0;
}

static int reference_fgetattr(const char *path, struct stat *out,
                             struct fuse_file_info *fi UNUSED)
{
    return reference_getattr(path, out);
}

static int reference_open(const char *path, struct fuse_file_info *fi)
{
    fprintf(stderr, "reference open flags=0x%x\n", fi->flags);
    if ((fi->flags & O_ACCMODE) != O_RDONLY ||
        (fi->flags & (O_TRUNC | O_CREAT)) != 0) return -EROFS;
    if (strcmp(path, "/") == 0) return -EISDIR;
    if (strcmp(path, fixture_path) != 0) return -ENOENT;
    return 0;
}

static int reference_read(const char *path, char *buffer, size_t size,
                          off_t offset, struct fuse_file_info *fi UNUSED)
{
    if (strcmp(path, fixture_path) != 0) return -ENOENT;
    if (offset < 0) return -EINVAL;
    if ((uint64_t)offset >= fixture_size) return 0;
    size_t available = fixture_size - (size_t)offset;
    if (size > available) size = available;
    memcpy(buffer, fixture_content + (size_t)offset, size);
    fprintf(stderr, "reference read offset=%lld size=%zu\n", (long long)offset, size);
    return (int)size;
}

static int reference_opendir(const char *path, struct fuse_file_info *fi)
{
    if ((fi->flags & O_ACCMODE) != O_RDONLY ||
        (fi->flags & (O_TRUNC | O_CREAT)) != 0) return -EROFS;
    if (strcmp(path, "/") != 0) return known_path(path) ? -ENOTDIR : -ENOENT;
    return 0;
}

static int reference_readdir(const char *path, void *buffer, fuse_fill_dir_t filler,
                             off_t offset, struct fuse_file_info *fi UNUSED)
{
    if (strcmp(path, "/") != 0) return -ENOENT;
    if (offset < 0) return -EINVAL;
    const char *entries[] = {".", "..", fixture_name};
    for (off_t i = offset; i < 3; ++i) {
        if (filler(buffer, entries[i], NULL, i + 1) != 0) break;
    }
    return 0;
}

static int reference_access(const char *path, int mode)
{
    if (!known_path(path)) return -ENOENT;
    if (mode & W_OK) return -EROFS;
    if ((mode & X_OK) && strcmp(path, "/") != 0) return -EACCES;
    return 0;
}

static int reference_statfs(const char *path UNUSED, struct statvfs *out)
{
    memset(out, 0, sizeof(*out));
    out->f_bsize = out->f_frsize = 4096;
    out->f_blocks = 1;
    out->f_files = 2;
    out->f_namemax = 255;
    out->f_flag = ST_RDONLY;
    return 0;
}

static int reference_release(const char *path UNUSED, struct fuse_file_info *fi UNUSED)
{
    return 0;
}

static int reference_fsync(const char *path UNUSED, int datasync UNUSED,
                           struct fuse_file_info *fi UNUSED)
{
    return 0;
}

static int reference_getxattr(const char *path, const char *name UNUSED,
                              char *value UNUSED, size_t size UNUSED,
                              uint32_t position UNUSED)
{
    return known_path(path) ? -ENOATTR : -ENOENT;
}

static int reference_listxattr(const char *path, char *list UNUSED, size_t size UNUSED)
{
    return known_path(path) ? 0 : -ENOENT;
}

/* Every exposed mutating callback refuses the operation, even with ro ignored. */
static int refuse_path(const char *p UNUSED) { return -EROFS; }
static int refuse_pair(const char *p UNUSED, const char *q UNUSED) { return -EROFS; }
static int refuse_mode(const char *p UNUSED, mode_t m UNUSED) { return -EROFS; }
static int refuse_mknod(const char *p UNUSED, mode_t m UNUSED, dev_t d UNUSED) { return -EROFS; }
static int refuse_chown(const char *p UNUSED, uid_t u UNUSED, gid_t g UNUSED) { return -EROFS; }
static int refuse_truncate(const char *p UNUSED, off_t o UNUSED) { return -EROFS; }
static int refuse_utime(const char *p UNUSED, struct utimbuf *t UNUSED) { return -EROFS; }
static int refuse_utimens(const char *p UNUSED, const struct timespec t[2] UNUSED) { return -EROFS; }
static int refuse_create(const char *p UNUSED, mode_t m UNUSED, struct fuse_file_info *f UNUSED) { return -EROFS; }
static int refuse_ftruncate(const char *p UNUSED, off_t o UNUSED, struct fuse_file_info *f UNUSED) { return -EROFS; }
static int refuse_write(const char *p UNUSED, const char *b UNUSED, size_t s UNUSED,
                        off_t o UNUSED, struct fuse_file_info *f UNUSED) { return -EROFS; }
static int refuse_write_buf(const char *p UNUSED, struct fuse_bufvec *b UNUSED,
                            off_t o UNUSED, struct fuse_file_info *f UNUSED) { return -EROFS; }
static int refuse_fallocate(const char *p UNUSED, int m UNUSED, off_t o UNUSED,
                            off_t l UNUSED, struct fuse_file_info *f UNUSED) { return -EROFS; }
static int refuse_setxattr(const char *p UNUSED, const char *n UNUSED, const char *v UNUSED,
                           size_t s UNUSED, int f UNUSED, uint32_t o UNUSED) { return -EROFS; }
static int refuse_renamex(const char *p UNUSED, const char *q UNUSED, unsigned int f UNUSED) { return -EROFS; }
static int refuse_exchange(const char *p UNUSED, const char *q UNUSED, unsigned long f UNUSED) { return -EROFS; }
static int refuse_time(const char *p UNUSED, const struct timespec *t UNUSED) { return -EROFS; }
static int refuse_chflags(const char *p UNUSED, uint32_t f UNUSED) { return -EROFS; }
static int refuse_setattr(const char *p UNUSED, struct setattr_x *a UNUSED) { return -EROFS; }
static int refuse_fsetattr(const char *p UNUSED, struct setattr_x *a UNUSED,
                           struct fuse_file_info *f UNUSED) { return -EROFS; }

static void *reference_init(struct fuse_conn_info *connection)
{
    fprintf(stderr, "reference init protocol=%u.%u uid=%u\n",
            connection->proto_major, connection->proto_minor, (unsigned int)owner_uid);
    return NULL;
}

static void reference_destroy(void *data UNUSED)
{
    fprintf(stderr, "reference destroy\n");
}

static const struct fuse_operations reference_operations = {
    .getattr = reference_getattr, .fgetattr = reference_fgetattr,
    .open = reference_open, .read = reference_read,
    .opendir = reference_opendir, .readdir = reference_readdir,
    .access = reference_access, .statfs = reference_statfs,
    .release = reference_release, .releasedir = reference_release,
    .flush = reference_release, .fsync = reference_fsync, .fsyncdir = reference_fsync,
    .getxattr = reference_getxattr, .listxattr = reference_listxattr,
    .mknod = refuse_mknod, .mkdir = refuse_mode, .unlink = refuse_path,
    .rmdir = refuse_path, .symlink = refuse_pair, .rename = refuse_pair,
    .link = refuse_pair, .chmod = refuse_mode, .chown = refuse_chown,
    .truncate = refuse_truncate, .utime = refuse_utime, .utimens = refuse_utimens,
    .create = refuse_create, .ftruncate = refuse_ftruncate,
    .write = refuse_write, .write_buf = refuse_write_buf, .fallocate = refuse_fallocate,
    .setxattr = refuse_setxattr, .removexattr = refuse_pair,
    .renamex = refuse_renamex, .setvolname = refuse_path, .exchange = refuse_exchange,
    .setbkuptime = refuse_time, .setchgtime = refuse_time, .setcrtime = refuse_time,
    .chflags = refuse_chflags, .setattr_x = refuse_setattr, .fsetattr_x = refuse_fsetattr,
    .init = reference_init, .destroy = reference_destroy,
};

int main(int argc, char **argv)
{
    if (getuid() == 0 || geteuid() == 0 || getuid() != geteuid() || getgid() != getegid()) {
        fprintf(stderr, "Run this development diagnostic as an ordinary user without mixed identities.\n");
        return 2;
    }
    if (argc != 5 || strcmp(argv[1], "--nonce") != 0 ||
        strcmp(argv[3], "--mountpoint") != 0 || !lower_hex(argv[2], 64) ||
        strncmp(argv[4], MOUNT_PREFIX, sizeof(MOUNT_PREFIX) - 1) != 0 ||
        !lower_hex(argv[4] + sizeof(MOUNT_PREFIX) - 1, 32)) {
        fprintf(stderr, "Usage: " REFERENCE_PROGRAM " --nonce <64 lowercase hex> --mountpoint " MOUNT_PREFIX "<32 lowercase hex>\n");
        return 2;
    }
    struct stat mount_stat;
    if (lstat(argv[4], &mount_stat) == 0 || errno != ENOENT) {
        fprintf(stderr, "Refusing existing or inaccessible mountpoint.\n");
        return 2;
    }
    owner_uid = getuid();
    owner_gid = getgid();
    creation_time = time(NULL);
    snprintf(fixture_name, sizeof(fixture_name), "fixture-%s.txt", argv[2]);
    snprintf(fixture_path, sizeof(fixture_path), "/%s", fixture_name);
    fixture_size = (size_t)snprintf(fixture_content, sizeof(fixture_content),
                                  "Nativol FSKit reference\n%s\n", argv[2]);
    char options[512];
    /* The reviewed libfuse dispatch accepts the fixed fskit/kernel backend. */
    int option_size = snprintf(options, sizeof(options),
        FIXED_OPTIONS ",fsname=" FILESYSTEM_PREFIX "%s,volname=%s",
        argv[2], argv[4] + sizeof("/Volumes/") - 1);
    if (option_size < 0 || (size_t)option_size >= sizeof(options)) return 2;
    char *fuse_argv[] = {REFERENCE_PROGRAM, "-f", "-s", "-o", options, argv[4], NULL};
    fprintf(stderr, "reference starting uid=%u fixed " REFERENCE_BACKEND "/local/read-only options\n", (unsigned int)owner_uid);
    const int result = fuse_main(6, fuse_argv, &reference_operations, NULL);
    fprintf(stderr, "reference fuse_main result=%d; exit alone does not prove a mount\n", result);
    return result;
}
