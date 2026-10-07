/* Unprivileged, read-only process metadata for the personal card diagnostic.
 * Public SDK structs stay in C; the exported record is our fixed-width ABI.
 */
#include <errno.h>
#include <libproc.h>
#include <stdint.h>
#include <string.h>
#include <sys/proc.h>
#include <sys/sysctl.h>

struct nativol_process_record {
    uint32_t version;
    uint32_t byte_size;
    int32_t pid;
    int32_t ppid;
    uint32_t uid;
    uint32_t ruid;
    uint32_t status;
    uint32_t reserved;
    uint64_t start_seconds;
    uint64_t start_microseconds;
    char path[4096];
};

_Static_assert(sizeof(struct nativol_process_record) == 4144, "Unexpected diagnostic ABI");

static int snapshot(pid_t pid, struct kinfo_proc *info)
{
    int mib[] = { CTL_KERN, KERN_PROC, KERN_PROC_PID, pid };
    size_t length = sizeof(*info);
    memset(info, 0, sizeof(*info));
    if (sysctl(mib, 4, info, &length, NULL, 0) != 0)
        return errno ? errno : EIO;
    if (length != sizeof(*info) || info->kp_proc.p_pid != pid
            || info->kp_proc.p_stat <= 0 || info->kp_proc.p_stat == SZOMB
            || info->kp_proc.p_starttime.tv_sec <= 0
            || info->kp_proc.p_starttime.tv_usec < 0
            || info->kp_proc.p_starttime.tv_usec >= 1000000)
        return ESRCH;
    return 0;
}

/* Return an errno value, with an all-zero output record on every failure. */
int nativol_process_identity(int32_t pid, struct nativol_process_record *output, size_t output_size)
{
    struct kinfo_proc before, after;
    struct nativol_process_record result;
    int error;
    if (output == NULL || output_size != sizeof(*output))
        return EINVAL;
    memset(output, 0, sizeof(*output));
    if (pid <= 1)
        return EINVAL;
    if ((error = snapshot(pid, &before)) != 0)
        return error;
    memset(&result, 0, sizeof(result));
    int length = proc_pidpath(pid, result.path, sizeof(result.path));
    if (length <= 0 || length >= (int)sizeof(result.path) || result.path[0] != '/'
            || memchr(result.path, '\0', sizeof(result.path)) == NULL)
        return errno ? errno : EIO;
    if ((error = snapshot(pid, &after)) != 0)
        return error;
    if (before.kp_eproc.e_ppid != after.kp_eproc.e_ppid
            || before.kp_eproc.e_ucred.cr_uid != after.kp_eproc.e_ucred.cr_uid
            || before.kp_eproc.e_pcred.p_ruid != after.kp_eproc.e_pcred.p_ruid
            || before.kp_proc.p_starttime.tv_sec != after.kp_proc.p_starttime.tv_sec
            || before.kp_proc.p_starttime.tv_usec != after.kp_proc.p_starttime.tv_usec)
        return EAGAIN;
    result.version = 1;
    result.byte_size = sizeof(result);
    result.pid = after.kp_proc.p_pid;
    result.ppid = after.kp_eproc.e_ppid;
    result.uid = after.kp_eproc.e_ucred.cr_uid;
    result.ruid = after.kp_eproc.e_pcred.p_ruid;
    result.status = after.kp_proc.p_stat;
    result.start_seconds = (uint64_t)after.kp_proc.p_starttime.tv_sec;
    result.start_microseconds = (uint64_t)after.kp_proc.p_starttime.tv_usec;
    *output = result;
    return 0;
}
