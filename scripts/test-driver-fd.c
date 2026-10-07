/* Regression for the Darwin inherited-fd device backend; no mount/device. */
#include "config.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include "device.h"

#define REQUIRE(test) do { if (!(test)) { \
    fprintf(stderr, "line %d: %s (errno=%d)\n", __LINE__, #test, errno); exit(1); \
} } while (0)

static struct ntfs_device *device(const char *name)
{
    struct ntfs_device *dev = ntfs_device_alloc(name, 0,
                                               &ntfs_device_default_io_ops, NULL);
    REQUIRE(dev != NULL);
    return dev;
}

static void refused(int flags)
{
    struct ntfs_device *dev = device("/dev/fd/3");
    REQUIRE(dev->d_ops->open(dev, flags) == -1);
    REQUIRE(!NDevOpen(dev));
    REQUIRE(dev->d_private == NULL);
    REQUIRE(ntfs_device_free(dev) == 0);
}

int main(int argc, char **argv)
{
    (void)argv;
    REQUIRE(argc == 1 && getuid() != 0 && getuid() == geteuid());
    char name[] = "fresh-fd-XXXXXX";
    int initial = mkstemp(name), held, ro, wo;
    struct stat original, duplicate;
    const char payload[] = "held descriptor remains writable";
    char readback[sizeof(payload)] = {0};
    REQUIRE(initial >= 0);
    REQUIRE(fstat(initial, &original) == 0 && S_ISREG(original.st_mode));
    held = fcntl(initial, F_DUPFD_CLOEXEC, 10);
    REQUIRE(held >= 10);
    REQUIRE(close(initial) == 0);
    REQUIRE(fchmod(held, 0600) == 0);
    ro = open(name, O_RDONLY | O_NOFOLLOW);
    wo = open(name, O_WRONLY | O_NOFOLLOW);
    REQUIRE(ro >= 0 && wo >= 0);
    /* Keep saved handles away from the exact child interface fd 3. */
    int saved_ro = fcntl(ro, F_DUPFD_CLOEXEC, 10);
    int saved_wo = fcntl(wo, F_DUPFD_CLOEXEC, 10);
    REQUIRE(saved_ro >= 10 && saved_wo >= 10);
    REQUIRE(close(ro) == 0 && close(wo) == 0);
    REQUIRE(dup2(held, 3) == 3);
    REQUIRE(fchmod(held, 0000) == 0);
    errno = 0;
    REQUIRE(open(name, O_RDWR | O_NOFOLLOW) == -1 && errno == EACCES);
    errno = 0;
    REQUIRE(open("/dev/fd/3", O_RDWR) == -1 && errno == EACCES);

    struct ntfs_device *dev = device("/dev/fd/3");
    REQUIRE(dev->d_ops->open(dev, O_RDWR) == 0);
    int duplicated_fd = *(int *)dev->d_private;
    REQUIRE(duplicated_fd >= 4 && duplicated_fd != 3);
    REQUIRE(fcntl(duplicated_fd, F_GETFD) & FD_CLOEXEC);
    REQUIRE(fstat(duplicated_fd, &duplicate) == 0);
    REQUIRE(original.st_dev == duplicate.st_dev && original.st_ino == duplicate.st_ino);
    REQUIRE(dev->d_ops->pwrite(dev, payload, sizeof(payload), 0) == sizeof(payload));
    REQUIRE(dev->d_ops->pread(dev, readback, sizeof(readback), 0) == sizeof(readback));
    REQUIRE(memcmp(payload, readback, sizeof(payload)) == 0);
    REQUIRE(dev->d_ops->close(dev) == 0);
    REQUIRE(ntfs_device_free(dev) == 0);
    REQUIRE(fstat(3, &duplicate) == 0); /* Device close leaves inherited handle. */

    dev = device("/dev/fd/3");
    REQUIRE(dev->d_ops->open(dev, O_RDONLY) == 0 && NDevReadOnly(dev));
    errno = 0;
    REQUIRE(dev->d_ops->pwrite(dev, payload, 1, 0) == -1 && errno == EROFS);
    REQUIRE(dev->d_ops->close(dev) == 0 && ntfs_device_free(dev) == 0);

    /* A duplicate must still acquire the stock per-process exclusive lock. */
    int lock_ready[2], lock_release[2], child_status;
    char signal_byte = 'L';
    REQUIRE(pipe(lock_ready) == 0 && pipe(lock_release) == 0);
    pid_t child = fork();
    REQUIRE(child >= 0);
    if (child == 0) {
        struct flock lock = {0};
        close(lock_ready[0]); close(lock_release[1]);
        lock.l_type = F_WRLCK;
        lock.l_whence = SEEK_SET;
        if (fcntl(held, F_SETLK, &lock) || write(lock_ready[1], "L", 1) != 1)
            _exit(2);
        if (read(lock_release[0], &signal_byte, 1) != 1) _exit(3);
        _exit(0);
    }
    REQUIRE(close(lock_ready[1]) == 0 && close(lock_release[0]) == 0);
    REQUIRE(read(lock_ready[0], &signal_byte, 1) == 1 && signal_byte == 'L');
    dev = device("/dev/fd/3");
    errno = 0;
    REQUIRE(dev->d_ops->open(dev, O_RDWR) == -1 && (errno == EACCES || errno == EAGAIN));
    REQUIRE(!NDevOpen(dev) && dev->d_private == NULL);
    REQUIRE(ntfs_device_free(dev) == 0 && fstat(3, &duplicate) == 0);
    REQUIRE(write(lock_release[1], "D", 1) == 1);
    REQUIRE(waitpid(child, &child_status, 0) == child);
    REQUIRE(WIFEXITED(child_status) && WEXITSTATUS(child_status) == 0);
    REQUIRE(close(lock_ready[0]) == 0 && close(lock_release[1]) == 0);
    dev = device("/dev/fd/3");
    REQUIRE(dev->d_ops->open(dev, O_RDWR) == 0);
    REQUIRE(dev->d_ops->close(dev) == 0 && ntfs_device_free(dev) == 0);

    /* Similar-looking/general paths keep stock permission enforcement. */
    dev = device(name);
    REQUIRE(dev->d_ops->open(dev, O_RDONLY) == -1 && !NDevOpen(dev));
    REQUIRE(ntfs_device_free(dev) == 0);
    refused(O_RDWR | O_TRUNC);
    refused(O_WRONLY);
    REQUIRE(fcntl(held, F_SETFL, O_APPEND) == 0);
    refused(O_RDWR);
    REQUIRE(fcntl(held, F_SETFL, 0) == 0);
    REQUIRE(dup2(saved_ro, 3) == 3);
    refused(O_RDWR);
    dev = device("/dev/fd/3");
    REQUIRE(dev->d_ops->open(dev, O_RDONLY) == 0);
    REQUIRE(dev->d_ops->close(dev) == 0 && ntfs_device_free(dev) == 0);
    REQUIRE(dup2(saved_wo, 3) == 3);
    refused(O_RDONLY);
    int pipefd[2];
    REQUIRE(pipe(pipefd) == 0);
    REQUIRE(dup2(pipefd[0], 3) == 3);
    refused(O_RDONLY);
    REQUIRE(close(3) == 0);
    refused(O_RDONLY);
    REQUIRE(close(pipefd[0]) == 0 && close(pipefd[1]) == 0);
    REQUIRE(fchmod(held, 0600) == 0);
    REQUIRE(close(held) == 0 && close(saved_ro) == 0 && close(saved_wo) == 0);
    REQUIRE(unlink(name) == 0);
    puts("PASS: revoked-path permissions, fd identity/CLOEXEC, RW data, RO denial, conflicting exclusive lock, unchanged generic path, incompatible/mutating/append/pipe/missing descriptor refusal");
    return 0;
}
