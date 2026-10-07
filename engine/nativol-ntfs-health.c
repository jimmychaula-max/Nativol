/* Personal deployment preflight. GPL-2.0-or-later, like linked libntfs-3g.
 * Reads only inherited fd 3. No target pathname, device open, repair or mount.
 * Caller must establish device identity and exclusive ownership independently. */
#include "config.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/disk.h>
#include <sys/stat.h>
#include <unistd.h>
#include "attrib.h"
#include "cache.h"
#include "device.h"
#include "dir.h"
#include "inode.h"
#include "layout.h"
#include "logfile.h"
#include "logging.h"
#include "volume.h"

static unsigned denied_operations;
static int readonly_open(struct ntfs_device *dev, int flags)
{
    struct stat info;
    int *fd;
    if ((flags & O_ACCMODE) != O_RDONLY || NDevOpen(dev)) {
        denied_operations++; errno = EROFS; return -1;
    }
    if (fstat(3, &info) || (!S_ISREG(info.st_mode) && !S_ISBLK(info.st_mode))) {
        errno = EINVAL; return -1;
    }
    fd = malloc(sizeof(*fd));
    if (!fd) return -1;
    *fd = fcntl(3, F_DUPFD_CLOEXEC, 4);
    if (*fd < 0) { free(fd); return -1; }
    dev->d_private = fd;
    NDevSetReadOnly(dev);
    NDevSetOpen(dev);
    if (S_ISBLK(info.st_mode)) NDevSetBlock(dev);
    return 0;
}

static s64 deny_write(struct ntfs_device *dev, const void *buf, s64 count)
{ (void)dev; (void)buf; (void)count; denied_operations++; errno = EROFS; return -1; }
static s64 deny_pwrite(struct ntfs_device *dev, const void *buf, s64 count, s64 offset)
{ (void)offset; return deny_write(dev, buf, count); }
static int readonly_sync(struct ntfs_device *dev)
{
    /* libntfs calls sync even during read-only teardown. Never issue fsync or
     * another writeback syscall; a clean read-only device needs no work. */
    if (NDevReadOnly(dev) && !NDevDirty(dev)) return 0;
    denied_operations++; errno = EROFS; return -1;
}
static int readonly_close(struct ntfs_device *dev)
{
    int result = readonly_sync(dev);
    if (close(*(int *)dev->d_private)) result = -1;
    free(dev->d_private); dev->d_private = NULL;
    NDevClearOpen(dev);
    return result;
}
static int readonly_ioctl(struct ntfs_device *dev, unsigned long request, void *arg)
{
    if (request != DKIOCGETBLOCKSIZE && request != DKIOCGETBLOCKCOUNT) {
        denied_operations++; errno = EROFS; return -1;
    }
    return ntfs_device_default_io_ops.ioctl(dev, request, arg);
}

/* The stock checker accepts unfamiliar nonzero headers. This profile does not. */
static int strict_hibernation_check(ntfs_volume *vol, const char **state)
{
    static const ntfschar name[] = {const_cpu_to_le16('h'),const_cpu_to_le16('i'),
        const_cpu_to_le16('b'),const_cpu_to_le16('e'),const_cpu_to_le16('r'),
        const_cpu_to_le16('f'),const_cpu_to_le16('i'),const_cpu_to_le16('l'),
        const_cpu_to_le16('.'),const_cpu_to_le16('s'),const_cpu_to_le16('y'),const_cpu_to_le16('s')};
    ntfs_inode *root = ntfs_inode_open(vol, FILE_root), *hiber = NULL;
    ntfs_attr *data = NULL;
    u64 reference;
    unsigned char header[4096];
    int success = 0, saved;
    *state = "unknown";
    if (!root) return -1;
    reference = ntfs_inode_lookup_by_name(root, name, 12);
    saved = errno;
    if (ntfs_inode_close(root)) return -1;
    if (reference == (u64)-1) {
        if (saved == ENOENT) { *state = "absent"; return 0; }
        return -1;
    }
    hiber = ntfs_inode_open(vol, MREF(reference));
    if (!hiber) return -1;
    data = ntfs_attr_open(hiber, AT_DATA, AT_UNNAMED, 0);
    if (!data || ntfs_attr_pread(data, 0, sizeof(header), header) != sizeof(header)) goto done;
    for (size_t i = 0; i < sizeof(header); i++) {
        if (header[i]) { *state = "nonzero-header"; goto done; }
    }
    *state = "zero-header";
    success = 1;
done:
    if (data) ntfs_attr_close(data);
    if (ntfs_inode_close(hiber)) success = 0;
    return success ? 0 : -1;
}

static int strict_logfile_check(ntfs_volume *vol, int *major, int *minor)
{
    ntfs_inode *inode = ntfs_inode_open(vol, FILE_LogFile);
    ntfs_attr *data = NULL;
    RESTART_PAGE_HEADER *page = NULL;
    int success = 0;
    if (!inode) return -1;
    data = ntfs_attr_open(inode, AT_DATA, AT_UNNAMED, 0);
    if (!data || !ntfs_check_logfile(data, &page) || !ntfs_is_logfile_clean(data, page)) goto done;
    if (page) {
        *major = le16_to_cpu(page->major_ver);
        *minor = le16_to_cpu(page->minor_ver);
        /* Initialized logs must use the reviewed 1.1 format. An empty freshly
         * formatted log has no restart page and is accepted by libntfs. */
        if (*major != 1 || *minor != 1) goto done;
    }
    success = 1;
done:
    free(page);
    if (data) ntfs_attr_close(data);
    if (ntfs_inode_close(inode)) success = 0;
    return success ? 0 : -1;
}

int main(int argc, char **argv)
{
    struct ntfs_device_operations ops = ntfs_device_default_io_ops;
    struct ntfs_device *device = NULL;
    ntfs_volume *volume = NULL;
    NTFS_BOOT_SECTOR boot;
    struct stat info;
    const char *reason = "invalid-descriptor", *hibernation = "unknown";
    unsigned major = 0, minor = 0, flags = 0;
    int logmajor = -1, logminor = -1, read_only = 0, clean_log = 0, ready = 0;
    uint64_t serial = 0;
    if (argc != 2 || strcmp(argv[1], "/dev/fd/3") != 0) {
        puts("{\"schemaVersion\":1,\"status\":\"refused\",\"readyForWrite\":false,\"reason\":\"expected-inherited-fd-3\",\"readOnly\":true}");
        return 2;
    }
    ntfs_log_set_handler(ntfs_log_handler_stderr);
    if (getuid() != geteuid() || getgid() != getegid() || fstat(3, &info)
        || (!S_ISREG(info.st_mode) && !S_ISBLK(info.st_mode))) goto done;
    if (pread(3, &boot, sizeof(boot), 0) != sizeof(boot)) goto done;
    ops.open = readonly_open; ops.close = readonly_close;
    ops.write = deny_write; ops.pwrite = deny_pwrite;
    ops.sync = readonly_sync; ops.ioctl = readonly_ioctl;
    device = ntfs_device_alloc("/dev/fd/3", 0, &ops, NULL);
    if (!device) { reason = "allocation-failed"; goto done; }
    volume = ntfs_device_mount(device, NTFS_MNT_RDONLY | NTFS_MNT_FORENSIC);
    if (!volume) { reason = "invalid-or-unreadable-ntfs"; ntfs_device_free(device); goto done; }
    ntfs_create_lru_caches(volume);
    /* Windows treats this system filename case-insensitively. This changes
     * only lookup behavior in memory, never volume metadata. */
    NVolClearCaseSensitive(volume);
    read_only = NVolReadOnly(volume) && NDevReadOnly(volume->dev);
    major = volume->major_ver; minor = volume->minor_ver;
    flags = le16_to_cpu(volume->flags);
    serial = le64_to_cpu(boot.volume_serial_number);
    if (!read_only) { reason = "read-only-invariant-failed"; goto close_volume; }
    if (major != 3 || minor != 1) { reason = "unsupported-ntfs-version"; goto close_volume; }
    /* DiscUtils names 0x0080 DisableShortNameCreation and uses it solely for
     * name creation policy. The ordinary NTFS-3G driver preserves these bits;
     * its separate maintenance tools' flag writer must not be used here.
     * https://github.com/DiscUtils/DiscUtils/blob/master/Library/DiscUtils.Ntfs/VolumeInformationFlags.cs
     * Accept this one informational bit, never dirty or other unknown bits. */
    if (flags & ~0x0080u) { reason = "dirty-or-unsupported-volume-flags"; goto close_volume; }
    if (strict_hibernation_check(volume, &hibernation)) { reason = "hibernated-or-unknown-hiberfile"; goto close_volume; }
    if (strict_logfile_check(volume, &logmajor, &logminor)) { reason = "unclean-or-unsupported-logfile"; goto close_volume; }
    clean_log = 1;
    reason = "clean-supported-ntfs";
    ready = 1;
close_volume:
    if (ntfs_umount(volume, FALSE)) { ready = 0; reason = "read-only-close-failed"; }
done:
    if (denied_operations) { ready = 0; reason = "unexpected-mutating-operation-denied"; }
    printf("{\"schemaVersion\":1,\"status\":\"%s\",\"readyForWrite\":%s,\"reason\":\"%s\","
           "\"readOnly\":true,\"ntfsReadOnlyVerified\":%s,\"ntfsMajor\":%u,\"ntfsMinor\":%u,"
           "\"volumeFlags\":%u,\"volumeSerial\":\"%016" PRIx64 "\",\"hibernation\":\"%s\","
           "\"logfileClean\":%s,\"logfileMajor\":%d,\"logfileMinor\":%d,\"deniedOperations\":%u}\n",
           ready ? "ready" : "refused", ready ? "true" : "false", reason,
           read_only ? "true" : "false", major, minor, flags, serial, hibernation,
           clean_log ? "true" : "false", logmajor, logminor, denied_operations);
    return ready ? 0 : 1;
}
