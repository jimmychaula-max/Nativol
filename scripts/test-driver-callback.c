/*
 * Focused regression harness for NTFS-3G 2026.9.28, compiled against its
 * corresponding patched source. The included implementation remains covered
 * by upstream's COPYING terms; this is a development test, not an app feature.
 *
 * No FUSE/FSKit mount is possible here: the FUSE mount call is replaced with an
 * aborting sentinel. The only source is fixture.ntfs created by the wrapper.
 */
#define main nativol_unused_driver_main
#define fuse_get_context nativol_test_fuse_get_context
#define fuse_mount nativol_forbidden_fuse_mount
#include NATIVOL_DRIVER_SOURCE
#undef main
#undef fuse_get_context
#undef fuse_mount

#include <CommonCrypto/CommonDigest.h>

#ifndef DISABLE_PLUGINS
#error This regression must exercise the disabled-plugins branch.
#endif

static struct fuse_context callback_context;
static unsigned callback_context_requests;

struct fuse_context *nativol_test_fuse_get_context(void)
{
    ++callback_context_requests;
    return &callback_context;
}

struct fuse_chan *nativol_forbidden_fuse_mount(const char *point,
                                             struct fuse_args *arguments)
{
    (void)point;
    (void)arguments;
    fputs("FAIL: an OS/FUSE mount was attempted\n", stderr);
    abort();
}

static void require(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s (errno=%d)\n", message, errno);
        exit(1);
    }
}

static void image_digest(int fd, unsigned char digest[CC_SHA256_DIGEST_LENGTH])
{
    CC_SHA256_CTX hash;
    unsigned char buffer[65536];
    off_t offset = 0;
    ssize_t count;
    CC_SHA256_Init(&hash);
    while ((count = pread(fd, buffer, sizeof(buffer), offset)) > 0) {
        CC_SHA256_Update(&hash, buffer, (CC_LONG)count);
        offset += count;
    }
    require(count == 0, "read owned fixture for SHA-256");
    CC_SHA256_Final(digest, &hash);
}

static void assert_absent(const char *path)
{
    errno = 0;
    ntfs_inode *inode = ntfs_pathname_to_inode(ctx->vol, NULL, path);
    require(inode == NULL && errno == ENOENT, "refused child does not exist");
}

int main(int argc, char **argv)
{
    (void)argv;
    require(argc == 1, "this harness accepts no device or image arguments");
    require(geteuid() != 0, "run as an ordinary user");
    struct stat directory, fixture;
    require(lstat(".", &directory) == 0 && S_ISDIR(directory.st_mode)
            && directory.st_uid == geteuid() && (directory.st_mode & 077) == 0,
            "fixture directory is private and owned by this user");
    int fd = open("fixture.ntfs", O_RDWR | O_NOFOLLOW);
    require(fd >= 0, "open wrapper-created fixture without following symlinks");
    require(fstat(fd, &fixture) == 0 && S_ISREG(fixture.st_mode)
            && fixture.st_uid == geteuid() && fixture.st_nlink == 1
            && fixture.st_size == 64 * 1024 * 1024
            && (fixture.st_mode & 077) == 0,
            "fixture is a private owned 64 MiB regular file");

    callback_context.uid = getuid();
    callback_context.gid = getgid();
    callback_context.pid = getpid();
    callback_context.umask = 077;
    require(ntfs_fuse_init() == 0, "initialize actual driver context");
    ctx->recover = FALSE;
    ctx->silent = FALSE;
    ctx->fmask = 077;
    ctx->dmask = 077;
    ctx->windows_names = TRUE;
    require(ntfs_open("fixture.ntfs") == NTFS_VOLUME_OK && ctx->vol,
            "open fresh regular image through libntfs only");
    require(!NVolReadOnly(ctx->vol), "owned image permits fixture setup");

    /* Positive control: exercise the real callback in an ordinary directory. */
    struct fuse_file_info info = { .flags = O_RDWR };
    require(ntfs_fuse_create("/control.bin", S_IFREG | 0600, 0, NULL, &info) == 0,
            "ordinary create callback succeeds");
    ntfs_inode *control = ntfs_pathname_to_inode(ctx->vol, NULL, "/control.bin");
    require(control != NULL, "positive-control inode exists");
    require(ntfs_inode_close(control) == 0, "close positive-control inode");

    /* This synthetic flag fixture targets the precise no-plugins branch. It
     * is deliberately not presented as a valid Windows junction fixture. */
    ntfs_inode *root = ntfs_pathname_to_inode(ctx->vol, NULL, "/");
    require(root != NULL, "open fixture root");
    ntfschar *name = NULL;
    int name_length = ntfs_mbstoucs("reparse", &name);
    require(name_length == 7, "encode synthetic parent name");
    ntfs_inode *parent = ntfs_create(root, const_cpu_to_le32(0), name,
                                    (u8)name_length, S_IFDIR);
    free(name);
    require(parent != NULL, "create synthetic parent directory");
    parent->flags |= FILE_ATTR_REPARSE_POINT;
    NInoSetDirty(parent);
    require(ntfs_inode_close_in_dir(parent, root) == 0, "store synthetic flag");
    require(ntfs_inode_close(root) == 0, "close fixture root");
    parent = ntfs_pathname_to_inode(ctx->vol, NULL, "/reparse");
    require(parent && (parent->flags & FILE_ATTR_REPARSE_POINT),
            "readback confirms branch-selecting reparse flag");
    require(ntfs_inode_close(parent) == 0, "close verified parent");
    require(ntfs_device_sync(ctx->vol->dev) == 0, "flush fixture setup");

    unsigned char before[CC_SHA256_DIGEST_LENGTH], after[CC_SHA256_DIGEST_LENGTH];
    image_digest(fd, before);
    for (unsigned attempt = 0; attempt != 8; ++attempt) {
        char path[64];
        snprintf(path, sizeof(path), "/reparse/refused-%u.bin", attempt);
        unsigned requests_before = callback_context_requests;
        info = (struct fuse_file_info) { .flags = O_RDWR };
        int result = ntfs_fuse_create(path, S_IFREG | 0600, 0, NULL, &info);
        require(callback_context_requests == requests_before + 1,
                "callback reached its security/creation branch");
        require(result == -EOPNOTSUPP, "reparse parent refuses creation");
        require(info.fh == 0, "refused callback leaves file handle empty");
        assert_absent(path);
    }
    require(ntfs_device_sync(ctx->vol->dev) == 0, "flush after refused operations");
    image_digest(fd, after);
    require(memcmp(before, after, sizeof(before)) == 0,
            "entire image unchanged by refused create calls");
    require(ntfs_umount(ctx->vol, FALSE) == 0, "close libntfs image handle");
    ctx->vol = ntfs_mount("fixture.ntfs", NTFS_MNT_RDONLY | NTFS_MNT_EXCLUSIVE);
    require(ctx->vol != NULL, "reopen fixture read-only without an OS mount");
    control = ntfs_pathname_to_inode(ctx->vol, NULL, "/control.bin");
    require(control != NULL, "positive control persists after reopening");
    require(ntfs_inode_close(control) == 0, "close reopened positive control");
    for (unsigned attempt = 0; attempt != 8; ++attempt) {
        char path[64];
        snprintf(path, sizeof(path), "/reparse/refused-%u.bin", attempt);
        assert_absent(path);
    }
    require(ntfs_umount(ctx->vol, FALSE) == 0, "close read-only image handle");
    ctx->vol = NULL;
    free(ctx);
    close(fd);
    puts("PASS: ordinary create, 8 reparse refusals, unchanged SHA-256, reopen verification; no OS mount");
    return 0;
}
