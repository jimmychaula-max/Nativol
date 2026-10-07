// Personal local gateway. Only the installed, pinned Nativol app can ask the
// separately pinned helper to manage its external NTFS drive. No device I/O here.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <SystemConfiguration/SystemConfiguration.h>
#import <CommonCrypto/CommonDigest.h>
#include <bsm/libbsm.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <libproc.h>
#include <poll.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>
#include <pwd.h>

static NSString *const Base = @"/Library/Application Support/Nativol";
static NSString *const Service = @"/Library/Application Support/Nativol/Service";
static NSString *const Socket = @"/Library/Application Support/Nativol/Service/control.sock";
static NSString *const App = @"/Applications/Nativol.app";
static NSString *const Identifier = @"app.nativol.preview";
static const NSUInteger MaximumFrame = 32768;
static NSMutableSet<NSNumber *> *children;
static NSMutableDictionary<NSNumber *,NSDictionary *> *mountReservations;

static void require(BOOL ok, NSString *reason) {
    if (!ok) @throw [NSException exceptionWithName:@"NativolServiceRefusal" reason:reason userInfo:nil];
}
static NSString *join(NSString *a, NSString *b) { return [a stringByAppendingPathComponent:b]; }
static BOOL matches(NSString *value, NSString *pattern) {
    return [value isKindOfClass:NSString.class] && NSEqualRanges([value rangeOfString:pattern options:NSRegularExpressionSearch], NSMakeRange(0,value.length));
}
static NSString *operationID(id value) {
    require([value isKindOfClass:NSString.class], @"Missing operation identity.");
    NSUUID *uuid = [[NSUUID alloc] initWithUUIDString:value];
    require(uuid && ![uuid.UUIDString isEqual:@"00000000-0000-0000-0000-000000000000"], @"Invalid operation identity.");
    return uuid.UUIDString;
}
static NSString *bootID(void) {
    char value[128]={0}; size_t size=sizeof value;
    require(sysctlbyname("kern.bootsessionuuid",value,&size,NULL,0)==0,@"Cannot identify current boot.");
    return [@(value) uppercaseString];
}
static void integer(id value, unsigned long long minimum, unsigned long long maximum) {
    require([value isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)value)!=CFBooleanGetTypeID()
            && !CFNumberIsFloatType((__bridge CFNumberRef)value) && matches([value stringValue],@"^[0-9]+$")
            && [value unsignedLongLongValue]>=minimum && [value unsignedLongLongValue]<=maximum,@"Invalid integer identity.");
}
static void protectedPath(NSString *path, BOOL directory, BOOL appPath) {
    struct stat s;
    require(lstat(path.fileSystemRepresentation,&s)==0 && s.st_uid==0
            && (directory?S_ISDIR(s.st_mode):S_ISREG(s.st_mode)),@"Administrator-protected files are required.");
    // Standard macOS /Applications is root:admin 0775. No other ancestor or
    // app-subtree write exception is accepted; dynamic code hash stays pinned.
    BOOL applications = appPath && [path isEqual:@"/Applications"] && s.st_gid==80 && (s.st_mode&0777)==0775;
    require(applications || !(s.st_mode&0022),@"Protected path is writable by another user.");
    if (![path isEqual:@"/"]) protectedPath(path.stringByDeletingLastPathComponent,YES,appPath);
}
static NSData *readProtected(NSString *path) {
    protectedPath(path,NO,NO);
    int fd=open(path.fileSystemRepresentation,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    require(fd>=0,@"Cannot read protected state.");
    struct stat s; BOOL valid=fstat(fd,&s)==0 && S_ISREG(s.st_mode) && s.st_uid==0
        && !(s.st_mode&0022) && s.st_nlink==1 && s.st_size>0 && s.st_size<=65536;
    if(!valid){close(fd);require(NO,@"Protected state exceeds limits.");}
    NSMutableData *data=[NSMutableData dataWithLength:(NSUInteger)s.st_size];
    ssize_t got=read(fd,data.mutableBytes,data.length);close(fd);
    require(got==(ssize_t)data.length,@"Protected state changed while reading.");return data;
}
static NSDictionary *dictionary(NSData *data) {
    require(data.length>0 && data.length<=65536,@"JSON exceeds limits.");
    id object=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    require([object isKindOfClass:NSDictionary.class],@"Expected a JSON object.");return object;
}
static NSString *hex(NSData *data) {
    NSMutableString *text=[NSMutableString string]; const unsigned char *bytes=data.bytes;
    for(NSUInteger i=0;i<data.length;i++)[text appendFormat:@"%02x",bytes[i]];return text;
}
static NSString *hashData(NSData *data) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256(data.bytes,(CC_LONG)data.length,digest);
    return hex([NSData dataWithBytes:digest length:sizeof digest]);
}
static NSString *hashFile(NSString *path) {
    protectedPath(path,NO,NO);
    int fd=open(path.fileSystemRepresentation,O_RDONLY|O_NOFOLLOW|O_CLOEXEC); require(fd>=0,@"Cannot verify backend file.");
    struct stat before,after; BOOL valid=fstat(fd,&before)==0 && before.st_nlink==1 && before.st_size>=0 && before.st_size<512*1024*1024;
    if(!valid){close(fd);require(NO,@"Invalid backend file size or link count.");}
    CC_SHA256_CTX context;CC_SHA256_Init(&context);unsigned char buffer[65536],digest[CC_SHA256_DIGEST_LENGTH];ssize_t count;
    while((count=read(fd,buffer,sizeof buffer))>0)CC_SHA256_Update(&context,buffer,(CC_LONG)count);
    valid=count==0 && fstat(fd,&after)==0 && before.st_size==after.st_size
        && before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec && before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec;
    close(fd);require(valid,@"Backend changed while verifying.");CC_SHA256_Final(digest,&context);
    return hex([NSData dataWithBytes:digest length:sizeof digest]);
}
static NSDictionary *configuration(void) {
    NSDictionary *config=dictionary(readProtected(join(Service,@"config.json")));
    require([[NSSet setWithArray:config.allKeys]isEqual:[NSSet setWithArray:@[@"schemaVersion",@"backendVersion",@"clientCDHash",@"clientIdentifier",@"serviceSHA256"]]],@"Unknown service configuration.");
    integer(config[@"schemaVersion"],1,1);
    require(matches(config[@"backendVersion"],@"^[0-9a-f]{64}$") && matches(config[@"clientCDHash"],@"^[0-9a-f]{40}$")
            && matches(config[@"serviceSHA256"],@"^[0-9a-f]{64}$") && [config[@"clientIdentifier"]isEqual:Identifier],@"Invalid service pins.");
    require([hashFile(join(Service,@"NativolService"))isEqual:config[@"serviceSHA256"]],@"Service executable differs from installed pin.");
    return config;
}
static NSString *verifyBackend(NSDictionary *config) {
    NSString *version=join(join(Base,@"Versions"),config[@"backendVersion"]);
    NSData *data=readProtected(join(version,@"manifest.json"));
    require([hashData(data)isEqual:config[@"backendVersion"]],@"Backend manifest hash differs.");
    NSDictionary *manifest=dictionary(data),*files=manifest[@"files"];
    NSSet *allowed=[NSSet setWithArray:@[@"bin/NativolHelper",@"bin/ntfs-3g",@"bin/nativol-ntfs-health",@"lib/libfuse.2.dylib",@"licenses/NTFS-3G-COPYING",@"licenses/NTFS-3G-COPYING.LIB"]];
    require([files isKindOfClass:NSDictionary.class] && [[NSSet setWithArray:files.allKeys]isEqual:allowed]
            && [manifest[@"profile"]isEqual:@"intel-external-test-v1"],@"Invalid backend manifest.");
    integer(manifest[@"schemaVersion"],1,1);
    for(NSString *name in files) require(matches(files[name],@"^[0-9a-f]{64}$") && [hashFile(join(version,name))isEqual:files[name]],@"Backend checksum differs.");
    SecStaticCodeRef code=NULL;
    OSStatus result=SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:join(version,@"bin/NativolHelper")],kSecCSDefaultFlags,&code);
    if(result==errSecSuccess)result=SecStaticCodeCheckValidity(code,kSecCSStrictValidate,NULL);
    if(code)CFRelease(code);require(result==errSecSuccess,@"Backend helper signature differs.");return version;
}
static void consoleIdentity(uid_t uid,gid_t gid) {
    uid_t console=0;gid_t consoleGID=0;NSString *name=CFBridgingRelease(SCDynamicStoreCopyConsoleUser(NULL,&console,&consoleGID));
    struct passwd *account=getpwuid(uid);
    require(uid>=501 && gid>0 && name.length && ![name isEqual:@"loginwindow"] && console==uid && account
            && account->pw_gid==gid,@"Only the active console user can manage this card.");
}
static void hardenedIdentity(NSDictionary *info,NSDictionary *config) {
    unsigned flags=[info[(__bridge NSString *)kSecCodeInfoFlags]unsignedIntValue];
    unsigned status=[info[(__bridge NSString *)kSecCodeInfoStatus]unsignedIntValue];
    NSDictionary *entitlements=info[(__bridge NSString *)kSecCodeInfoEntitlementsDict];
    require((flags&kSecCodeSignatureRuntime) && (status&kSecCodeStatusValid) && !(status&kSecCodeStatusDebugged)
            && (!entitlements || ([entitlements isKindOfClass:NSDictionary.class] && entitlements.count==0)),@"Caller must use the hardened app without debugging or runtime exceptions.");
    NSData *unique=info[(__bridge NSString *)kSecCodeInfoUnique];
    require([unique isKindOfClass:NSData.class] && unique.length==20
            && [hex(unique)isEqual:config[@"clientCDHash"]],@"Caller signature changed.");
}
static NSDictionary *authenticate(int fd,NSDictionary *config) {
    audit_token_t token; socklen_t length=sizeof token;
    require(getsockopt(fd,SOL_LOCAL,LOCAL_PEERTOKEN,&token,&length)==0 && length==sizeof token,@"Cannot authenticate local caller.");
    uid_t uid=audit_token_to_euid(token);gid_t gid=audit_token_to_egid(token);
    require(uid==audit_token_to_ruid(token) && gid==audit_token_to_rgid(token),@"Mixed caller identity refused.");
    consoleIdentity(uid,gid);
    NSData *audit=[NSData dataWithBytes:&token length:sizeof token];
    NSDictionary *attributes=@{(__bridge NSString *)kSecGuestAttributeAudit:audit};
    SecCodeRef guest=NULL;SecRequirementRef requirement=NULL;CFDictionaryRef information=NULL;
    NSString *rule=[NSString stringWithFormat:@"identifier \"%@\" and cdhash H\"%@\"",Identifier,config[@"clientCDHash"]];
    OSStatus result=SecRequirementCreateWithString((__bridge CFStringRef)rule,kSecCSDefaultFlags,&requirement);
    if(result==errSecSuccess)result=SecCodeCopyGuestWithAttributes(NULL,(__bridge CFDictionaryRef)attributes,kSecCSDefaultFlags,&guest);
    if(result==errSecSuccess)result=SecCodeCheckValidity(guest,kSecCSStrictValidate,requirement);
    if(result==errSecSuccess)result=SecCodeCopySigningInformation(guest,kSecCSSigningInformation|kSecCSDynamicInformation,&information);
    NSDictionary *info=CFBridgingRelease(information);
    if(guest)CFRelease(guest);if(requirement)CFRelease(requirement);
    require(result==errSecSuccess,@"Only the installed Nativol app can use its background service.");
    NSString *path=[info[(__bridge NSString *)kSecCodeInfoMainExecutable]path];
    require([path isEqual:join(App,@"Contents/MacOS/Nativol")],@"Open the installed copy in Applications.");
    protectedPath(path,NO,YES);
    hardenedIdentity(info,config);
    return @{@"uid":@(uid),@"gid":@(gid),@"pid":@(audit_token_to_pid(token))};
}
static void validateEnvelope(NSDictionary *request,uid_t uid,gid_t gid,NSString *boot) {
    integer(request[@"schemaVersion"],1,1);
    NSString *verb=request[@"verb"];require([verb isKindOfClass:NSString.class],@"Missing fixed action.");
    NSArray *keys;
    if([verb isEqual:@"status"])keys=@[@"schemaVersion",@"verb"];
    else if([verb isEqual:@"mount-rw"]||[verb isEqual:@"mount-ro"]) {
        keys=@[@"schemaVersion",@"verb",@"request"];
        NSDictionary *mount=request[@"request"];require([mount isKindOfClass:NSDictionary.class],@"Missing mount request.");
        integer(mount[@"requestedUID"],uid,uid);integer(mount[@"requestedGID"],gid,gid);
        require([mount[@"bootSessionUUID"]isEqual:boot],@"Mount request belongs to another boot.");
        operationID(mount[@"operationId"]);
        // The existing helper validates the complete lease, profile, IOKit
        // identity, raw descriptor, serial, and health before any mount.
    } else {
        require([verb isEqual:@"unmount"]||[verb isEqual:@"eject"],@"Unknown service action.");
        keys=@[@"schemaVersion",@"verb",@"operationId"];operationID(request[@"operationId"]);
    }
    require([[NSSet setWithArray:request.allKeys]isEqual:[NSSet setWithArray:keys]],@"Unknown or missing envelope fields.");
}
static BOOL liveHelper(NSDictionary *state,NSString *version) {
    pid_t pid=[state[@"helperPID"]intValue];struct proc_bsdinfo info={0};char path[PROC_PIDPATHINFO_MAXSIZE]={0};
    return pid>0 && proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof info)==sizeof info && info.pbi_uid==0
        && proc_pidpath(pid,path,sizeof path)>0 && [@(path)isEqual:join(version,@"bin/NativolHelper")]
        && [state[@"updatedAt"]doubleValue]>=(double)info.pbi_start_tvsec;
}
static void validateOwnedState(NSDictionary *state,NSString *operation,uid_t uid,NSString *version,NSString *boot) {
    integer(state[@"schemaVersion"],1,1);integer(state[@"ownerUID"],uid,uid);
    require([state[@"operationId"]isEqual:operation] && [state[@"bootSessionUUID"]isEqual:boot]
            && [state[@"helperVersionDir"]isEqual:version] && [state[@"targetProfile"]isEqual:@"intel-external-test-v1"]
            && [state[@"source"]isEqual:@"/dev/fd/3"] && [state[@"filesystem"]isEqual:@"macfuse"]
            && [state[@"mountPath"]isEqual:join(@"/Volumes",[@"Nativol-" stringByAppendingString:operation])],@"Session does not belong to this user, boot, or installed backend.");
}
static void checkIdle(void) {
    NSString *directory=join(Base,@"State");protectedPath(directory,YES,NO);
    NSArray *paths=[[NSFileManager defaultManager]contentsOfDirectoryAtPath:directory error:NULL];
    require(paths && paths.count<=4096,@"Cannot bound existing sessions.");
    NSString *boot=bootID();NSSet *active=[NSSet setWithArray:@[@"preparing",@"mounted",@"unmounting",@"attention"]];
    for(NSString *name in paths)if([name.pathExtension isEqual:@"json"]) {
        NSDictionary *s=dictionary(readProtected(join(directory,name)));
        require(!([s[@"bootSessionUUID"]isEqual:boot] && [active containsObject:s[@"state"]]),@"Stop the active drive session before setup or another mount.");
    }
    int capacity=proc_listallpids(NULL,0);require(capacity>0 && capacity<65536,@"Cannot bound helper processes.");
    NSMutableData *pids=[NSMutableData dataWithLength:(NSUInteger)(capacity+256)*sizeof(pid_t)];
    int count=proc_listallpids(pids.mutableBytes,(int)pids.length);require(count>0 && count<capacity+256,@"Process list changed; retry setup.");
    pid_t *values=pids.mutableBytes;
    for(int i=0;i<count;i++) {char path[PROC_PIDPATHINFO_MAXSIZE]={0};struct proc_bsdinfo info={0};
        if(proc_pidpath(values[i],path,sizeof path)>0 && [@(path)hasPrefix:join(Base,@"Versions/")]
           && [@(path).lastPathComponent isEqual:@"NativolHelper"]
           && proc_pidinfo(values[i],PROC_PIDTBSDINFO,0,&info,sizeof info)==sizeof info && info.pbi_uid==0)
            require(NO,@"An existing helper still owns the drive. Finish its session first.");
    }
}
// Pure admission policy: each physical drive owns its own supervisor, while
// uncertain state can never authorize a second owner of the same attachment.
static unsigned long long stateRegistryID(id value) {
    require(matches(value,@"^[1-9][0-9]{0,19}$"),@"An active drive session has incomplete identity.");
    errno=0;unsigned long long result=strtoull([value UTF8String],NULL,10);
    require(errno!=ERANGE&&result>0,@"An active drive identity exceeds limits.");return result;
}
static void admitMount(NSDictionary *request,NSArray *records,NSString *boot) {
    integer(request[@"mediaRegistryID"],1,UINT64_MAX);integer(request[@"partitionRegistryID"],1,UINT64_MAX);
    unsigned long long media=[request[@"mediaRegistryID"]unsignedLongLongValue],partition=[request[@"partitionRegistryID"]unsignedLongLongValue];
    require(media!=partition,@"Ambiguous requested attachment.");
    require(records.count<=4096,@"Cannot bound existing sessions.");
    NSSet *active=[NSSet setWithArray:@[@"preparing",@"mounted",@"unmounting",@"attention"]];
    NSSet *terminal=[NSSet setWithArray:@[@"failed",@"ejected",@"unmounted",@"disconnected",@"external-unmounted"]];
    NSUInteger count=0;NSMutableSet *mediaIDs=[NSMutableSet new],*partitionIDs=[NSMutableSet new];
    for(NSDictionary *record in records) {
        require([record isKindOfClass:NSDictionary.class]&&[record[@"bootSessionUUID"]isKindOfClass:NSString.class],@"Cannot identify an existing session.");
        if(![record[@"bootSessionUUID"]isEqual:boot])continue;
        if([terminal containsObject:record[@"state"]])continue;
        require([active containsObject:record[@"state"]],@"An existing session has unknown state.");
        NSNumber *m=@(stateRegistryID(record[@"mediaRegistryID"])),*p=@(stateRegistryID(record[@"partitionRegistryID"]));
        require(![m isEqual:p]&&![mediaIDs containsObject:m]&&![partitionIDs containsObject:p],@"Existing drive sessions have ambiguous identity.");
        require(m.unsignedLongLongValue!=media&&p.unsignedLongLongValue!=partition,@"This physical drive already has an active or unresolved session.");
        [mediaIDs addObject:m];[partitionIDs addObject:p];count++;
    }
    require(count<8,@"Eight drives are already managed. Eject a drive before enabling another.");
}
static void checkMountAvailability(NSDictionary *request) {
    NSString *directory=join(Base,@"State");protectedPath(directory,YES,NO);
    NSArray *paths=[[NSFileManager defaultManager]contentsOfDirectoryAtPath:directory error:NULL];
    require(paths&&paths.count<=4096,@"Cannot bound existing sessions.");
    NSMutableArray *records=[NSMutableArray new];
    for(NSString *name in paths)if([name.pathExtension isEqual:@"json"])[records addObject:dictionary(readProtected(join(directory,name)))];
    // Kernel approval/loading can outlive a socket reply. Reserve that pending
    // attachment until its helper publishes a record or is reaped.
    NSMutableSet *published=[NSMutableSet new];
    for(NSDictionary *record in records)if(record[@"operationId"])[published addObject:record[@"operationId"]];
    for(NSDictionary *pending in mountReservations.allValues)
        if(![published containsObject:pending[@"operationId"]])[records addObject:pending];
    admitMount(request,records,bootID());
}
static pid_t launch(NSString *version,NSString *verb,NSString *argument) {
    // Keep capacity for stop/eject commands even with eight mount supervisors.
    require(children.count<32,@"Background service is busy.");
    NSString *exe=join(version,@"bin/NativolHelper");
    char *args[]={strdup(exe.fileSystemRepresentation),strdup(verb.UTF8String),strdup(argument.UTF8String),NULL};
    char *environment[]={"PATH=/usr/bin:/bin:/usr/sbin:/sbin","LC_ALL=C",NULL};
    int nullfd=open("/dev/null",O_RDWR|O_CLOEXEC);require(nullfd>=0,@"Cannot prepare helper input/output.");
    int descriptorLimit=getdtablesize();require(descriptorLimit>0 && descriptorLimit<=1048576,@"Unexpected descriptor limit.");
    pid_t pid=fork();
    if(pid==0) {
        // No Objective-C/Foundation after fork. The supervisor gets its own
        // session and no output pipe owned by this replaceable gateway.
        if(setsid()<0 || dup2(nullfd,0)<0 || dup2(nullfd,1)<0 || dup2(nullfd,2)<0)_exit(125);
        for(int fd=3;fd<descriptorLimit;fd++)close(fd);
        execve(args[0],args,environment);_exit(126);
    }
    close(nullfd);for(int i=0;i<3;i++)free(args[i]);require(pid>0,@"Cannot start the fixed backend helper.");
    [children addObject:@(pid)];return pid;
}
static void reap(void) {int result;pid_t pid;while((pid=waitpid(-1,&result,WNOHANG))>0){[children removeObject:@(pid)];[mountReservations removeObjectForKey:@(pid)];}}
static NSDictionary *dispatch(NSDictionary *request,NSDictionary *peer,NSDictionary *config) {
    uid_t uid=[peer[@"uid"]unsignedIntValue];gid_t gid=[peer[@"gid"]unsignedIntValue];NSString *boot=bootID();
    validateEnvelope(request,uid,gid,boot);consoleIdentity(uid,gid);
    NSString *version=verifyBackend(config),*verb=request[@"verb"];
    if([verb isEqual:@"status"])return @{@"ok":@YES,@"status":@"ready",@"backendVersion":config[@"backendVersion"]};
    NSString *operation,*argument;
    BOOL mounting=[verb hasPrefix:@"mount-"];
    if(mounting) {
        checkMountAvailability(request[@"request"]);operation=operationID(request[@"request"][@"operationId"]);
        NSData *data=[NSJSONSerialization dataWithJSONObject:request[@"request"] options:NSJSONWritingSortedKeys error:NULL];
        require(data.length>0 && data.length<16384,@"Mount request exceeds limit.");argument=[data base64EncodedStringWithOptions:0];
    } else {
        operation=operationID(request[@"operationId"]);argument=operation;
        NSDictionary *s=dictionary(readProtected(join(join(Base,@"State"),[operation stringByAppendingString:@".json"])));
        validateOwnedState(s,operation,uid,version,boot);
        require([s[@"state"]isEqual:@"mounted"] && liveHelper(s,version),@"The owning mounted session is no longer available.");
    }
    pid_t pid=launch(version,verb,argument);NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:20];
    if(mounting) {
        NSDictionary *r=request[@"request"];
        if(!mountReservations)mountReservations=[NSMutableDictionary new];
        mountReservations[@(pid)]=@{@"operationId":operation,@"bootSessionUUID":boot,@"state":@"preparing",
            @"mediaRegistryID":[r[@"mediaRegistryID"]stringValue],@"partitionRegistryID":[r[@"partitionRegistryID"]stringValue]};
    }
    NSString *statusPath=join(join(Base,@"State"),[operation stringByAppendingString:@".json"]);
    while(deadline.timeIntervalSinceNow>0) {
        int result=0;pid_t ended=waitpid(pid,&result,WNOHANG);
        if(ended==pid) {
            [children removeObject:@(pid)];
            [mountReservations removeObjectForKey:@(pid)];
            require(WIFEXITED(result)&&WEXITSTATUS(result)==0,@"The helper refused the operation. Check the drive status in Activity.");
            require(!mounting,@"The mount helper exited before publishing a live session.");
            return @{@"ok":@YES,@"status":@"submitted"};
        }
        if(mounting && access(statusPath.fileSystemRepresentation,F_OK)==0) {
            NSDictionary *s=dictionary(readProtected(statusPath));validateOwnedState(s,operation,uid,version,boot);
            require([s[@"helperPID"]intValue]==pid,@"Operation state belongs to another helper.");
            require(![s[@"state"]isEqual:@"failed"] && ![s[@"state"]isEqual:@"attention"],s[@"message"]?:@"Helper refused mount.");
            return @{@"ok":@YES,@"status":@"submitted"};
        }
        usleep(50000);
    }
    // Never terminate a supervisor whose device work may already have begun.
    require(NO,@"Helper startup is still pending. Check Activity before retrying.");return nil;
}
static void transfer(int fd,void *buffer,size_t size,BOOL writing,NSTimeInterval timeout) {
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:timeout];size_t offset=0;
    while(offset<size) {
        NSTimeInterval remaining=deadline.timeIntervalSinceNow;require(remaining>0,@"Local message timed out.");
        struct pollfd item={.fd=fd,.events=writing?POLLOUT:POLLIN};
        int ready=poll(&item,1,(int)(remaining*1000));
        if(ready<0 && errno==EINTR)continue;
        require(ready>0 && !(item.revents&(POLLERR|POLLNVAL)),@"Local connection failed.");
        ssize_t count=writing?send(fd,(char *)buffer+offset,size-offset,0):recv(fd,(char *)buffer+offset,size-offset,0);
        if(count<0 && (errno==EINTR||errno==EAGAIN))continue;
        require(count>0,@"Local connection closed.");offset+=(size_t)count;
    }
}
static NSDictionary *receive(int fd) {
    uint32_t size;transfer(fd,&size,sizeof size,NO,3);size=ntohl(size);
    require(size>0 && size<=MaximumFrame,@"Local request exceeds limit.");
    NSMutableData *data=[NSMutableData dataWithLength:size];transfer(fd,data.mutableBytes,size,NO,3);return dictionary(data);
}
static void respond(int fd,NSDictionary *response) {
    NSData *data=[NSJSONSerialization dataWithJSONObject:response options:0 error:NULL];
    require(data.length>0 && data.length<=MaximumFrame,@"Local response exceeds limit.");
    uint32_t size=htonl((uint32_t)data.length);transfer(fd,&size,sizeof size,YES,2);transfer(fd,(void *)data.bytes,data.length,YES,2);
}

#ifndef NATIVOL_SERVICE_TEST
int main(int argc,const char **argv) {
    @autoreleasepool { @try {
        require(getuid()==0 && geteuid()==0,@"Install this background service using Nativol Setup.");
        umask(077);children=[NSMutableSet set];signal(SIGPIPE,SIG_IGN);
        if(argc==2 && strcmp(argv[1],"--check-idle")==0){checkIdle();puts("idle");return 0;}
        require(argc==1,@"No background-service arguments are accepted.");
        protectedPath(Service,YES,NO);configuration();
        int lock=open(join(Service,@"service.lock").fileSystemRepresentation,O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
        struct stat lockInfo;
        require(lock>=0 && fstat(lock,&lockInfo)==0 && S_ISREG(lockInfo.st_mode) && lockInfo.st_uid==0
                && lockInfo.st_nlink==1 && (lockInfo.st_mode&0777)==0600
                && flock(lock,LOCK_EX|LOCK_NB)==0,@"Another service instance or invalid lock is present.");
        struct stat old;
        if(lstat(Socket.fileSystemRepresentation,&old)==0){require(S_ISSOCK(old.st_mode)&&old.st_uid==0,@"Unexpected socket object.");require(unlink(Socket.fileSystemRepresentation)==0,@"Cannot replace stale socket.");}
        int listener=socket(AF_UNIX,SOCK_STREAM,0);require(listener>=0,@"Cannot create local socket.");
        fcntl(listener,F_SETFD,FD_CLOEXEC);struct sockaddr_un address={0};address.sun_family=AF_UNIX;
        strlcpy(address.sun_path,Socket.fileSystemRepresentation,sizeof address.sun_path);address.sun_len=sizeof address;
        require(bind(listener,(struct sockaddr *)&address,sizeof address)==0 && chmod(Socket.fileSystemRepresentation,0666)==0 && listen(listener,8)==0,@"Cannot publish local service.");
        for(;;) { @autoreleasepool {
            reap();struct pollfd item={.fd=listener,.events=POLLIN};int ready=poll(&item,1,1000);
            if(ready<=0)continue;int fd=accept(listener,NULL,NULL);if(fd<0)continue;
            fcntl(fd,F_SETFD,FD_CLOEXEC);fcntl(fd,F_SETFL,O_NONBLOCK);int noSig=1;setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&noSig,sizeof noSig);
            @try {NSDictionary *config=configuration(),*peer=authenticate(fd,config),*request=receive(fd);
                // Re-authenticate after framing, including current console identity.
                require([authenticate(fd,config)isEqual:peer],@"Caller changed during request.");respond(fd,dispatch(request,peer,config));}
            @catch(NSException *error){@try{respond(fd,@{@"ok":@NO,@"error":error.reason?:@"Request refused."});}@catch(NSException *ignored){}}
            close(fd);
        }}
    } @catch(NSException *error){fprintf(stderr,"NativolService: %s\n",error.reason.UTF8String);return 1;} }
}
#endif
