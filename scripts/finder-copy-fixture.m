/* Fixed local image-fixture copy check. No target arguments or mount actions. */
#import <Foundation/Foundation.h>
#include <copyfile.h>
#include <errno.h>
#include <sys/stat.h>
#include <limits.h>
#include <sys/mount.h>
#include <unistd.h>

int main(int argc, char **argv) {
    (void)argv;
    @autoreleasepool {
        struct statfs fs;
        if (argc != 1 || getuid() == 0 || statfs(".", &fs)
            || strcmp(fs.f_fstypename, "macfuse")
            || !strstr(fs.f_mntfromname, "/.local-engine/finder-images/")) return 2;
        NSString *host = [[NSString stringWithUTF8String:argv[0]] stringByDeletingLastPathComponent];
        NSString *variant = [[[NSFileManager defaultManager] currentDirectoryPath] lastPathComponent];
        struct stat hostInfo;
        struct statfs hostFS;
        char resolved[PATH_MAX];
        if ((![@"finder-baseline" isEqual:variant] && ![@"finder-native" isEqual:variant])
            || ![host containsString:@"/.local-engine/finder-images/finder-"]
            || !realpath(host.fileSystemRepresentation, resolved)
            || strcmp(resolved, host.fileSystemRepresentation)
            || lstat(resolved, &hostInfo) || !S_ISDIR(hostInfo.st_mode)
            || hostInfo.st_uid != getuid() || (hostInfo.st_mode & 0777) != 0700
            || statfs(resolved, &hostFS) || strcmp(hostFS.f_fstypename, "apfs")) return 2;
        NSError *copyError = nil, *resourceError = nil;
        id resource = nil;
        NSURL *url = [NSURL fileURLWithPath:[[NSFileManager defaultManager]
                            currentDirectoryPath]];
        url = [url URLByAppendingPathComponent:@"source.bin"];
        BOOL resourceOK = [url getResourceValue:&resource forKey:NSURLLocalizedNameKey error:&resourceError];
        BOOL copied = [[NSFileManager defaultManager] copyItemAtPath:@"source.bin"
                            toPath:@"foundation-copy.bin" error:&copyError];
        errno = 0;
        int copyResult = copyfile("source.bin", "copyfile-copy.bin", NULL, COPYFILE_ALL | COPYFILE_EXCL);
        int copyErrno = errno;
        NSError *fromAPFSError = nil, *toAPFSError = nil;
        BOOL fromAPFS = [[NSFileManager defaultManager]
            copyItemAtPath:[host stringByAppendingPathComponent:@"host-source.bin"]
            toPath:@"apfs-copy.bin" error:&fromAPFSError];
        BOOL toAPFS = fromAPFS && [[NSFileManager defaultManager] copyItemAtPath:@"apfs-copy.bin"
            toPath:[host stringByAppendingPathComponent:[variant stringByAppendingString:@"-roundtrip.bin"]]
            error:&toAPFSError];
        errno = 0;
        int fromAPFSCopyfile = copyfile([[host stringByAppendingPathComponent:@"host-source.bin"] fileSystemRepresentation],
            "apfs-copyfile.bin", NULL, COPYFILE_ALL | COPYFILE_EXCL);
        int fromAPFSCopyfileErrno = errno;
        NSError *underlying = copyError.userInfo[NSUnderlyingErrorKey];
        NSError *fromUnderlying = fromAPFSError.userInfo[NSUnderlyingErrorKey];
        NSDictionary *report = @{@"foundationCopyPassed": @(copied),
            @"foundationErrorDomain": copyError.domain ?: @"", @"foundationErrorCode": @(copyError.code),
            @"foundationUnderlyingDomain": underlying.domain ?: @"",
            @"foundationUnderlyingCode": @(underlying.code),
            @"copyfilePassed": @((BOOL)(copyResult == 0)), @"copyfileErrno": @(copyErrno),
            @"apfsToNTFSPassed": @(fromAPFS), @"ntfsToAPFSPassed": @(toAPFS),
            @"apfsToNTFSErrorCode": @(fromAPFSError.code),
            @"apfsToNTFSUnderlyingCode": @(fromUnderlying.code),
            @"ntfsToAPFSErrorCode": @(toAPFSError.code),
            @"apfsCopyfilePassed": @((BOOL)(fromAPFSCopyfile == 0)),
            @"apfsCopyfileErrno": @(fromAPFSCopyfileErrno),
            @"urlResourcePassed": @(resourceOK), @"urlResourceErrorCode": @(resourceError.code)};
        NSData *data = [NSJSONSerialization dataWithJSONObject:report options:0 error:NULL];
        fwrite(data.bytes, 1, data.length, stdout); putchar('\n');
        return 0;
    }
}
