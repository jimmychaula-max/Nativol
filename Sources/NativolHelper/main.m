// Personal development helper. The only writable target is the enrolled 4 GB
// external-drive policy. All client metadata is checked again before opening it.
#import <Foundation/Foundation.h>
#import <DiskArbitration/DiskArbitration.h>
#import <SystemConfiguration/SystemConfiguration.h>
#import <IOKit/IOKitLib.h>
#import <IOKit/IOBSD.h>
#import <CommonCrypto/CommonDigest.h>
#import <sys/mount.h>
#import <sys/stat.h>
#import <sys/sysctl.h>
#import <sys/file.h>
#import <sys/wait.h>
#import <libproc.h>
#import <fcntl.h>
#import <pwd.h>
#import <grp.h>
#import <unistd.h>
#import <math.h>

static NSString *const Base = @"/Library/Application Support/Nativol";
static NSString *const Private = @"/private/var/db/nativol";
static NSString *const KernelPath = @"/Library/Filesystems/macfuse.fs/Contents/Extensions/14/macfuse.kext";
static NSString *const KernelIdentifier = @"io.macfuse.filesystems.macfuse.23";
static NSString *versionDir, *operation, *privateDir, *statePath, *mountPath;
static NSMutableDictionary *state;
static NSDictionary *activeRequest;
static DASessionRef session;
static DADiskRef claimedDisk;
static int deviceFD = -1, lockFD = -1;
static pid_t driverPID = -1;
static pid_t inspectorPID = -1;
static BOOL hasMounted = NO, claimed = NO;
static BOOL originallyMountedRO = NO, wasUnmounted = NO, driverEverStarted = NO;
static BOOL arbitrationUncertain = NO;
static struct statfs ownedMount;

static void require(BOOL ok, NSString *message) {
    if (!ok) @throw [NSException exceptionWithName:@"NativolRefusal" reason:message userInfo:nil];
}
static NSString *join(NSString *a, NSString *b) { return [a stringByAppendingPathComponent:b]; }
static NSString *bootID(void) {
    char b[128] = {0}; size_t n = sizeof b;
    require(sysctlbyname("kern.bootsessionuuid", b, &n, NULL, 0) == 0, @"Cannot identify this boot.");
    return [[NSString stringWithUTF8String:b] uppercaseString];
}
static NSString *uuid(NSString *s) {
    require([s isKindOfClass:NSString.class], @"Missing operation identity.");
    NSUUID *u = [[NSUUID alloc] initWithUUIDString:s];
    require(u != nil && ![u.UUIDString isEqual:@"00000000-0000-0000-0000-000000000000"], @"Invalid operation identity."); return u.UUIDString;
}
static NSData *jsonData(id object) {
    NSError *error; NSData *d = [NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingSortedKeys error:&error];
    require(d != nil, @"Cannot encode state."); return d;
}
static id json(NSData *d) {
    require(d.length > 0 && d.length <= 65536, @"Request or state exceeds its size limit.");
    id o = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
    require([o isKindOfClass:NSDictionary.class], @"Invalid request or state."); return o;
}
static void protectedPath(NSString *p, BOOL directory) {
    struct stat s;
    require(lstat(p.fileSystemRepresentation, &s) == 0 && s.st_uid == 0 && !(s.st_mode & 0022)
            && (directory ? S_ISDIR(s.st_mode) : S_ISREG(s.st_mode)), @"Helper files must be owned and protected by macOS administrator installation.");
    if (![p isEqualToString:@"/"]) protectedPath(p.stringByDeletingLastPathComponent, YES);
}
static NSData *readProtected(NSString *p) {
    protectedPath(p, NO);
    int fd = open(p.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    require(fd >= 0, @"Cannot open protected state.");
    struct stat s; BOOL valid = fstat(fd, &s) == 0 && S_ISREG(s.st_mode) && s.st_uid == 0 && !(s.st_mode & 0022) && s.st_size > 0 && s.st_size <= 65536;
    if (!valid) { close(fd); require(NO, @"Invalid protected state file."); }
    NSMutableData *d = [NSMutableData dataWithLength:(NSUInteger)s.st_size];
    ssize_t got = read(fd, d.mutableBytes, d.length); close(fd);
    require(got == (ssize_t)d.length, @"State changed while reading."); return d;
}
static NSString *sha(NSString *p) {
    protectedPath(p, NO);
    int fd = open(p.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    require(fd >= 0, @"Cannot open protected executable.");
    CC_SHA256_CTX c; CC_SHA256_Init(&c); unsigned char buf[65536], digest[CC_SHA256_DIGEST_LENGTH]; ssize_t n;
    while ((n = read(fd, buf, sizeof buf)) > 0) CC_SHA256_Update(&c, buf, (CC_LONG)n);
    close(fd); require(n == 0, @"Cannot hash protected executable."); CC_SHA256_Final(digest, &c);
    NSMutableString *s = [NSMutableString string]; for (int i=0;i<sizeof digest;i++) [s appendFormat:@"%02x",digest[i]]; return s;
}
static void atomicState(void) {
    state[@"updatedAt"] = @([[NSDate date] timeIntervalSince1970]);
    NSData *data = jsonData(state);
    NSString *temporary = [statePath stringByAppendingFormat:@".%@.tmp", NSUUID.UUID.UUIDString];
    int fd = open(temporary.fileSystemRepresentation, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0644);
    require(fd >= 0, @"Cannot create helper status.");
    BOOL ok = write(fd, data.bytes, data.length) == (ssize_t)data.length && fchmod(fd,0644)==0 && fsync(fd)==0;
    close(fd); require(ok && rename(temporary.fileSystemRepresentation,statePath.fileSystemRepresentation)==0, @"Cannot publish helper status.");
}
static NSDictionary *run(NSString *exe, NSArray *args, NSTimeInterval timeout) {
    NSTask *task = [NSTask new]; task.executableURL = [NSURL fileURLWithPath:exe]; task.arguments = args;
    task.environment = @{@"PATH":@"/usr/bin:/bin:/usr/sbin:/sbin",@"LC_ALL":@"C"};
    // Metadata commands used here produce small outputs. A temporary file avoids
    // pipe backpressure and never passes command output to a shell.
    char temp[] = "/private/tmp/nativol-command.XXXXXX"; int fd = mkstemp(temp); require(fd>=0,@"Cannot stage command output."); unlink(temp);
    NSFileHandle *out = [[NSFileHandle alloc] initWithFileDescriptor:fd closeOnDealloc:YES];
    task.standardOutput=out; task.standardError=out; task.standardInput=[NSFileHandle fileHandleWithNullDevice];
    NSError *error; require([task launchAndReturnError:&error], @"Cannot start a required macOS command.");
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:timeout];
    while(task.running && deadline.timeIntervalSinceNow>0) { CFRunLoopRunInMode(kCFRunLoopDefaultMode,.05,false); }
    if(task.running) { [task terminate]; require(NO,@"macOS command timed out. No force-unmount was requested."); }
    struct stat output;
    require(fstat(fd,&output)==0&&output.st_size>=0&&output.st_size<=65536&&lseek(fd,0,SEEK_SET)==0,
            @"Required macOS command output was incomplete or exceeded its size limit.");
    char buffer[65537]; ssize_t n=read(fd,buffer,65536);
    require(n==output.st_size,@"Required macOS command output changed while reading.");
    NSString *text=n>=0?[[NSString alloc]initWithBytes:buffer length:(NSUInteger)n encoding:NSUTF8StringEncoding]:nil;
    require(text!=nil,@"Required macOS command output was not valid UTF-8.");
    return @{@"exit":@(task.terminationStatus),@"text":text};
}
static id prop(io_registry_entry_t media, NSString *key) {
    return CFBridgingRelease(IORegistryEntryCreateCFProperty(media,(__bridge CFStringRef)key,kCFAllocatorDefault,0));
}
static uint64_t rid(io_registry_entry_t o) { uint64_t v=0; if(o) IORegistryEntryGetRegistryEntryID(o,&v); return v; }
static NSString *cfuuid(id o) {
    if(!o)return nil;
    if(CFGetTypeID((__bridge CFTypeRef)o)==CFUUIDGetTypeID()) return CFBridgingRelease(CFUUIDCreateString(NULL,(__bridge CFUUIDRef)o));
    return [o isKindOfClass:NSString.class]? [o uppercaseString]:nil;
}
static NSDictionary *snapshot(NSString *bsd) {
    require([bsd isKindOfClass:NSString.class] && [bsd rangeOfString:@"^disk[0-9]+s[0-9]+$" options:NSRegularExpressionSearch].location!=NSNotFound,@"Only a partition can be selected.");
    DADiskRef d=DADiskCreateFromBSDName(NULL,session,bsd.UTF8String); require(d!=NULL,@"Selected partition is unavailable.");
    DADiskRef whole=DADiskCopyWholeDisk(d);
    NSDictionary *desc=CFBridgingRelease(DADiskCopyDescription(d));
    NSDictionary *parent=whole?CFBridgingRelease(DADiskCopyDescription(whole)):nil;
    io_service_t media=DADiskCopyIOMedia(d), pm=whole?DADiskCopyIOMedia(whole):0;
    require(media&&pm&&desc&&parent,@"Incomplete device identity.");
    uint64_t childID=rid(media),parentID=rid(pm); BOOL relation=NO;
    io_registry_entry_t cursor=media; IOObjectRetain(cursor);
    for(int i=0;i<16;i++) { io_registry_entry_t up=0; if(IORegistryEntryGetParentEntry(cursor,kIOServicePlane,&up)!=KERN_SUCCESS)break;
        IOObjectRelease(cursor); cursor=up;
        if(IOObjectConformsTo(cursor,"IOMedia")) { relation=rid(cursor)==parentID; break; }
    } IOObjectRelease(cursor);
    io_iterator_t iterator=0; unsigned count=0;
    if(IORegistryEntryCreateIterator(pm,kIOServicePlane,kIORegistryIterateRecursively,&iterator)==KERN_SUCCESS) {
        io_registry_entry_t child; while((child=IOIteratorNext(iterator))) { if(IOObjectConformsTo(child,"IOMedia"))count++; IOObjectRelease(child); } IOObjectRelease(iterator);
    }
    NSString *wholeBSD=DADiskGetBSDName(whole)?@(DADiskGetBSDName(whole)):@"";
    NSString *protocol=desc[(__bridge NSString*)kDADiskDescriptionDeviceProtocolKey]?:parent[(__bridge NSString*)kDADiskDescriptionDeviceProtocolKey];
    NSMutableDictionary *r=[@{
        @"bsdName":bsd,@"parentBSDName":wholeBSD,@"partitionRegistryID":@(childID),@"mediaRegistryID":@(parentID),
        @"partitionOffsetBytes":prop(media,@"Base")?:@0,@"capacityBytes":prop(media,@"Size")?:@0,
        @"mediaCapacityBytes":prop(pm,@"Size")?:@0,@"blockSizeBytes":prop(media,@"Preferred Block Size")?:@0,
        @"parentBlockSizeBytes":prop(pm,@"Preferred Block Size")?:@0,@"deviceProtocol":protocol?:@"",
        @"partitionMap":prop(pm,@"Content")?:@"",@"parentRelationVerified":@(relation),@"partitionCount":@(count),
        @"internal":desc[(__bridge NSString*)kDADiskDescriptionDeviceInternalKey]?:@YES,
        @"parentInternal":parent[(__bridge NSString*)kDADiskDescriptionDeviceInternalKey]?:@YES,
        @"whole":prop(media,@"Whole")?:@YES,@"parentWhole":prop(pm,@"Whole")?:@NO,
        @"filesystem":desc[(__bridge NSString*)kDADiskDescriptionVolumeKindKey]?:@"",
        @"major":prop(media,@"BSD Major")?:@(-1),@"minor":prop(media,@"BSD Minor")?:@(-1),
        @"writableMedia":prop(media,@"Writable")?:@NO
    }mutableCopy];
    NSString *pu=cfuuid(desc[(__bridge NSString*)kDADiskDescriptionMediaUUIDKey])?:cfuuid(desc[(__bridge NSString*)kDADiskDescriptionVolumeUUIDKey]);
    NSString *mu=cfuuid(parent[(__bridge NSString*)kDADiskDescriptionMediaUUIDKey]);
    if(pu)r[@"partitionUUID"]=pu; if(mu)r[@"mediaUUID"]=mu;
    IOObjectRelease(media); IOObjectRelease(pm); CFRelease(whole); CFRelease(d); return r;
}
static void integerField(NSDictionary *r, NSString *key, unsigned long long minimum, unsigned long long maximum);
static void profile(NSDictionary *s) {
    require([s[@"internal"]isEqual:@NO]&&[s[@"parentInternal"]isEqual:@NO]&&[s[@"whole"]isEqual:@NO]&&[s[@"parentWhole"]isEqual:@YES]&&[s[@"writableMedia"]isEqual:@YES],@"Only writable external physical NTFS partitions are supported.");
    require([s[@"filesystem"]isEqual:@"ntfs"]&&[@[@"USB",@"Thunderbolt"]containsObject:s[@"deviceProtocol"]]&&[@[@"FDisk_partition_scheme",@"GUID_partition_scheme"]containsObject:s[@"partitionMap"]],@"The drive's format, partition map or physical transport is unsupported.");
    for(NSString *key in @[@"partitionRegistryID",@"mediaRegistryID"])integerField(s,key,1,UINT64_MAX);
    require([s[@"partitionRegistryID"]unsignedLongLongValue]>0&&[s[@"mediaRegistryID"]unsignedLongLongValue]>0&&![s[@"partitionRegistryID"]isEqual:s[@"mediaRegistryID"]]&&[s[@"parentRelationVerified"]boolValue]&&[s[@"partitionCount"]intValue]==1,@"Ambiguous partition topology.");
    for(NSString *key in @[@"partitionOffsetBytes",@"capacityBytes",@"mediaCapacityBytes",@"blockSizeBytes",@"parentBlockSizeBytes"])integerField(s,key,1,INT64_MAX);
    uint64_t offset=[s[@"partitionOffsetBytes"]unsignedLongLongValue],length=[s[@"capacityBytes"]unsignedLongLongValue],whole=[s[@"mediaCapacityBytes"]unsignedLongLongValue],block=[s[@"blockSizeBytes"]unsignedLongLongValue];
    require((block==512||block==4096)&&block==[s[@"parentBlockSizeBytes"]unsignedLongLongValue]
            && offset<whole&&length<=whole-offset&&offset%block==0&&length%block==0&&whole%block==0,@"The drive's sector size or partition geometry cannot be safely supported.");
}
static NSArray *identityKeys(void) {
    return @[@"bsdName",@"parentBSDName",@"partitionRegistryID",@"mediaRegistryID",@"partitionOffsetBytes",@"capacityBytes",@"mediaCapacityBytes",@"blockSizeBytes",@"parentBlockSizeBytes",@"deviceProtocol",@"partitionMap",@"parentRelationVerified",@"partitionCount",@"partitionUUID",@"mediaUUID"];
}
static NSDictionary *checkIdentity(NSDictionary *request) {
    require([request[@"bootSessionUUID"]isEqual:bootID()],@"The Mac restarted. Select the connected card again.");
    NSDictionary *current=snapshot(request[@"bsdName"]); profile(current);
    for(NSString *key in identityKeys()) require((current[key]==nil&&request[key]==nil)||[current[key]isEqual:request[key]],@"The selected attachment changed. Reconnect and select it again.");
    return current;
}
// This routine is deliberately pure so malformed requests can be tested before
// any console-user lookup, Disk Arbitration request, descriptor open or command.
static void integerField(NSDictionary *r, NSString *key, unsigned long long minimum, unsigned long long maximum) {
    id n=r[key];
    require([n isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)n)!=CFBooleanGetTypeID(),
            [NSString stringWithFormat:@"%@ must be an integer.",key]);
    require(!CFNumberIsFloatType((__bridge CFNumberRef)n), @"Fractional or floating-point identity fields are not accepted.");
    NSString *text=[n stringValue], *upper=[NSString stringWithFormat:@"%llu",maximum];
    require([text rangeOfString:@"^[0-9]+$" options:NSRegularExpressionSearch].location!=NSNotFound
            && (text.length<upper.length || (text.length==upper.length && [text compare:upper]!=NSOrderedDescending)),
            @"Integer identity field is outside its allowed range.");
    require([n unsignedLongLongValue]>=minimum,@"Integer identity field is below its allowed range.");
}
static void booleanField(NSDictionary *r, NSString *key) {
    id value=r[key];
    require(value!=nil && CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID(),
            [NSString stringWithFormat:@"%@ must be a JSON boolean.",key]);
}
static void validateRequestShape(NSDictionary *r) {
    require([r isKindOfClass:NSDictionary.class],@"Request must be an object.");
    NSSet *required=[NSSet setWithArray:@[@"schemaVersion",@"operationId",@"bootSessionUUID",@"createdAt",@"expiresAt",@"bsdName",@"parentBSDName",@"partitionRegistryID",@"mediaRegistryID",@"partitionOffsetBytes",@"capacityBytes",@"mediaCapacityBytes",@"blockSizeBytes",@"parentBlockSizeBytes",@"sessionID",@"requestedUID",@"requestedGID",@"deviceProtocol",@"partitionMap",@"parentRelationVerified",@"partitionCount",@"protectedVolume"]];
    NSMutableSet *allowed=[required mutableCopy]; [allowed addObjectsFromArray:@[@"partitionUUID",@"mediaUUID"]];
    for(id key in r)require([allowed containsObject:key],@"Unknown request field.");
    for(NSString *key in required)require(r[key]!=nil&&r[key]!=NSNull.null,@"Required request field is missing.");
    integerField(r,@"schemaVersion",1,1);
    integerField(r,@"requestedUID",501,UINT32_MAX); integerField(r,@"requestedGID",1,UINT32_MAX);
    for(NSString *key in @[@"partitionRegistryID",@"mediaRegistryID"])integerField(r,key,1,UINT64_MAX);
    for(NSString *key in @[@"capacityBytes",@"mediaCapacityBytes",@"blockSizeBytes",@"parentBlockSizeBytes"])
        integerField(r,key,1,INT64_MAX);
    integerField(r,@"partitionOffsetBytes",0,INT64_MAX); integerField(r,@"partitionCount",1,INT32_MAX);
    booleanField(r,@"protectedVolume"); booleanField(r,@"parentRelationVerified");
    for(NSString *key in @[@"operationId",@"bootSessionUUID",@"sessionID",@"partitionUUID",@"mediaUUID"])
        if(r[key])uuid(r[key]);
    for(NSString *key in @[@"bsdName",@"parentBSDName",@"deviceProtocol",@"partitionMap",@"createdAt",@"expiresAt"])
        require([r[key]isKindOfClass:NSString.class]&&[r[key]length]>0&&[r[key]length]<=128,@"Invalid request string.");
    NSString *bsd=r[@"bsdName"],*parent=r[@"parentBSDName"];
    require(NSEqualRanges([bsd rangeOfString:@"^disk[0-9]+s[0-9]+$" options:NSRegularExpressionSearch],NSMakeRange(0,bsd.length))
            && NSEqualRanges([parent rangeOfString:@"^disk[0-9]+$" options:NSRegularExpressionSearch],NSMakeRange(0,parent.length))
            && [bsd hasPrefix:[parent stringByAppendingString:@"s"]],@"Invalid partition address.");
    NSISO8601DateFormatter *f=[NSISO8601DateFormatter new];
    NSDate *made=[f dateFromString:r[@"createdAt"]],*expires=[f dateFromString:r[@"expiresAt"]];
    NSTimeInterval lifetime=[expires timeIntervalSinceDate:made];
    require(made&&expires&&isfinite(lifetime)&&lifetime>0&&lifetime<=120,@"Invalid request lifetime.");
}
static void checkRequest(NSDictionary *r) {
    validateRequestShape(r);
    require([r[@"schemaVersion"]isEqual:@1]&&[r[@"protectedVolume"]isEqual:@NO],@"Invalid or protected request.");
    uuid(r[@"operationId"]); uuid(r[@"sessionID"]);
    uid_t uid; gid_t gid; NSString *console=CFBridgingRelease(SCDynamicStoreCopyConsoleUser(NULL,&uid,&gid));
    require(console.length>0&&uid>=501&&[r[@"requestedUID"]unsignedIntValue]==uid&&[r[@"requestedGID"]unsignedIntValue]==gid,@"The requesting user must be the active local Mac user.");
    NSISO8601DateFormatter *f=[NSISO8601DateFormatter new]; NSDate *made=[f dateFromString:r[@"createdAt"]], *expires=[f dateFromString:r[@"expiresAt"]];
    require(made&&expires&&made.timeIntervalSinceNow<=0&&made.timeIntervalSinceNow>=-120&&expires.timeIntervalSinceNow>0&&[expires timeIntervalSinceDate:made]>0&&[expires timeIntervalSinceDate:made]<=120,@"Selection expired during authorization. Select the drive again.");
    checkIdentity(r);
}
static NSDictionary *prepare(NSString *bsd) {
    NSMutableDictionary *r=[snapshot(bsd)mutableCopy]; profile(r);
    for(NSString *key in @[@"internal",@"parentInternal",@"whole",@"parentWhole",@"filesystem",@"major",@"minor",@"writableMedia"]) [r removeObjectForKey:key];
    r[@"schemaVersion"]=@1; r[@"operationId"]=NSUUID.UUID.UUIDString; r[@"sessionID"]=NSUUID.UUID.UUIDString;
    r[@"bootSessionUUID"]=bootID(); r[@"requestedUID"]=@(getuid()); r[@"requestedGID"]=@(getgid()); r[@"protectedVolume"]=@NO;
    NSISO8601DateFormatter *f=[NSISO8601DateFormatter new]; NSDate *now=[NSDate date]; r[@"createdAt"]=[f stringFromDate:now]; r[@"expiresAt"]=[f stringFromDate:[now dateByAddingTimeInterval:120]]; return r;
}
typedef struct { BOOL done; DAReturn result; } DAResult;
static void complete(DADiskRef d,DADissenterRef dissent,void *context) { DAResult *r=context; r->result=dissent?DADissenterGetStatus(dissent):0; r->done=YES; }
static DADissenterRef refuseRelease(DADiskRef d,void *context) { return DADissenterCreate(NULL,kDAReturnBusy,CFSTR("Nativol is managing this card. Stop writing in Nativol first.")); }
static void waitDA(DAResult *r) {
    arbitrationUncertain=YES;
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:20];
    while(!r->done&&deadline.timeIntervalSinceNow>0)CFRunLoopRunInMode(kCFRunLoopDefaultMode,.05,false);
    // Heap contexts are deliberately retained if a callback times out.
    require(r->done,@"Disk Arbitration did not complete. No forced operation was requested.");
    arbitrationUncertain=NO;
    require(r->result==0,[NSString stringWithFormat:@"macOS declined the disk operation (0x%x). Close files on the drive and retry.",r->result]);
}
static void claimAndUnmount(NSDictionary *r) {
    claimedDisk=DADiskCreateFromBSDName(NULL,session,[r[@"bsdName"]UTF8String]); require(claimedDisk!=NULL,@"Card disappeared.");
    DAResult *result=calloc(1,sizeof *result); DADiskClaim(claimedDisk,kDADiskClaimOptionDefault,refuseRelease,NULL,complete,result); waitDA(result); claimed=YES; free(result);
    checkIdentity(r);
    struct statfs *table; int count=getmntinfo(&table,MNT_NOWAIT); require(count>0,@"Cannot inspect active mounts.");
    NSString *source=[@"/dev/" stringByAppendingString:r[@"bsdName"]]; unsigned matches=0;
    for(int i=0;i<count;i++)if([source isEqual:@(table[i].f_mntfromname)]) { matches++; require(strcmp(table[i].f_fstypename,"ntfs")==0&&(table[i].f_flags&MNT_RDONLY),@"Another writable driver already owns this card."); }
    require(matches<=1,@"Ambiguous current card mount.");
    originallyMountedRO=matches==1;
    state[@"originallyMountedReadOnly"]=@(originallyMountedRO);
    if(matches) { result=calloc(1,sizeof *result); DADiskUnmount(claimedDisk,kDADiskUnmountOptionDefault,complete,result); waitDA(result);wasUnmounted=YES;state[@"initialUnmountCompleted"]=@YES;free(result); }
    checkIdentity(r);
    count=getmntinfo(&table,MNT_NOWAIT); require(count>0,@"Cannot verify unmount.");
    for(int i=0;i<count;i++)require(![source isEqual:@(table[i].f_mntfromname)],@"The drive remains mounted.");
}
static NSString *deviceOpenFailureMessage(int code) {
    NSString *detail=[NSError errorWithDomain:NSPOSIXErrorDomain code:code userInfo:nil].localizedDescription;
    if(code==EPERM||code==EACCES)
        return [NSString stringWithFormat:@"Allow NativolHelper in Full Disk Access in System Settings > Privacy & Security, then retry from Nativol. macOS refused device access (%@; errno %d).",detail,code];
    return [NSString stringWithFormat:@"Cannot open the selected drive: %@ (errno %d).",detail,code];
}
static int openCard(NSDictionary *r,BOOL writable) {
    NSDictionary *s=checkIdentity(r); NSString *p=[@"/dev/" stringByAppendingString:r[@"bsdName"]];
    int fd=open(p.fileSystemRepresentation,(writable?O_RDWR:O_RDONLY)|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0) { int code=errno;state[@"deviceOpenErrno"]=@(code);require(NO,deviceOpenFailureMessage(code)); }
    struct stat st; BOOL ok=fstat(fd,&st)==0&&S_ISBLK(st.st_mode)&&major(st.st_rdev)==[s[@"major"]intValue]&&minor(st.st_rdev)==[s[@"minor"]intValue];
    @try { require(ok,@"Opened device does not match the selected partition."); checkIdentity(r); }
    @catch(NSException *e) { close(fd); @throw e; }
    return fd;
}
static pid_t spawnUser(NSString *exe,NSArray *arguments,int fd,NSString *output,uid_t uid,gid_t gid) {
    int log=open(output.fileSystemRepresentation,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600); require(log>=0,@"Cannot create driver log.");
    // fd 3 is reserved for the device. Never let a log descriptor alias it:
    // otherwise dup2(device, 3) could redirect log writes into the drive.
    int highLog=fcntl(log,F_DUPFD_CLOEXEC,4); close(log); log=highLog;
    require(log>=4 && log!=fd,@"Cannot isolate the driver log descriptor.");
    NSMutableArray *strings=[NSMutableArray arrayWithObject:exe]; [strings addObjectsFromArray:arguments];
    char **args=calloc(strings.count+1,sizeof(char*)); for(NSUInteger i=0;i<strings.count;i++)args[i]=strdup([strings[i]UTF8String]);
    NSArray *env=@[@"PATH=/usr/bin:/bin:/usr/sbin:/sbin",@"LC_ALL=C",[NSString stringWithFormat:@"DYLD_LIBRARY_PATH=%@",join(versionDir,@"lib")],@"HOME=/private/tmp"];
    char **envp=calloc(env.count+1,sizeof(char*)); for(NSUInteger i=0;i<env.count;i++)envp[i]=strdup([env[i]UTF8String]);
    const char *path=args[0]; int fdLimit=getdtablesize(); pid_t pid=fork();
    if(pid==0) {
        // Only async-signal-safe operations between fork and exec. No Foundation.
        if(dup2(fd,3)<0||dup2(log,STDOUT_FILENO)<0||dup2(log,STDERR_FILENO)<0)_exit(120);
        int nullfd=open("/dev/null",O_RDONLY); if(nullfd<0||dup2(nullfd,STDIN_FILENO)<0)_exit(121);
        if(fcntl(3,F_SETFD,0)<0||setgroups(1,&gid)<0||setgid(gid)<0||setuid(uid)<0||getuid()!=uid||geteuid()!=uid)_exit(122);
        // Other helper descriptors are opened CLOEXEC; only the verified device
        // and standard streams remain across exec.
        for(int i=4;i<fdLimit;i++)close(i); execve(path,args,envp); _exit(127);
    }
    close(log); for(NSUInteger i=0;i<strings.count;i++)free(args[i]);free(args); for(NSUInteger i=0;i<env.count;i++)free(envp[i]);free(envp);
    require(pid>0,@"Cannot start the user-owned driver."); return pid;
}
static pid_t waitChild(pid_t pid, int *status, NSTimeInterval seconds) {
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:seconds];
    for(;;) {
        pid_t done=waitpid(pid,status,WNOHANG);
        if(done>0 || (done<0 && errno!=EINTR))return done;
        if(deadline.timeIntervalSinceNow<=0)return 0;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode,.05,false);
    }
}
static void recordExit(NSString *name,int status) {
    state[[name stringByAppendingString:@"Exited"]]=@YES;
    if(WIFEXITED(status))state[[name stringByAppendingString:@"Exit"]]=@(WEXITSTATUS(status));
    if(WIFSIGNALED(status))state[[name stringByAppendingString:@"Signal"]]=@(WTERMSIG(status));
}
static void stopInspector(void) {
    if(inspectorPID<=0)return;
    int status=0; pid_t done=waitpid(inspectorPID,&status,WNOHANG);
    if(done==inspectorPID) { recordExit(@"inspector",status);inspectorPID=-1;return; }
    // Signal only an unreaped child of this supervisor; a stale numeric PID is
    // never enough. Uncertain wait errors retain the claim and session lock.
    if(done!=0) { state[@"inspectorStillRunning"]=@YES;return; }
    kill(inspectorPID,SIGTERM); done=waitChild(inspectorPID,&status,2);
    if(done==0) { kill(inspectorPID,SIGKILL);done=waitChild(inspectorPID,&status,2); }
    if(done==inspectorPID) { recordExit(@"inspector",status);inspectorPID=-1; }
    else state[@"inspectorStillRunning"]=@YES;
}
static BOOL driverRunning(void) {
    require(driverPID>0,@"No live owned driver is recorded.");
    int status=0; pid_t done=waitpid(driverPID,&status,WNOHANG);
    if(done==0 || (done<0&&errno==EINTR))return YES;
    if(done==driverPID) { recordExit(@"driver",status);driverPID=-1;return NO; }
    require(NO,@"Cannot verify the filesystem driver's process state. Keep the drive connected.");return NO;
}
static void inspectedSerial(NSDictionary *inspection) {
    // Read from the already bound descriptor. General drive support does not
    // enroll a particular serial, but malformed or missing identity fails closed.
    NSString *serial=inspection[@"volumeSerial"];
    require([serial isKindOfClass:NSString.class]&&serial.length==16
            &&[serial rangeOfString:@"^[0-9a-fA-F]{16}$" options:NSRegularExpressionSearch].location!=NSNotFound
            &&![serial isEqual:@"0000000000000000"],@"The NTFS inspector could not establish a valid filesystem identity.");
}
static NSDictionary *health(NSDictionary *r) {
    require(deviceFD>=0,@"No bound device descriptor for health inspection.");
    NSString *log=join(privateDir,@"health.json");
    inspectorPID=spawnUser(join(versionDir,@"bin/nativol-ntfs-health"),@[@"/dev/fd/3"],deviceFD,log,[r[@"requestedUID"]unsignedIntValue],[r[@"requestedGID"]unsignedIntValue]);
    state[@"inspectorPID"]=@(inspectorPID); atomicState();
    int exitStatus=0; pid_t done=waitChild(inspectorPID,&exitStatus,20);
    if(done!=inspectorPID) {
        stopInspector();
        require(NO,inspectorPID>0?@"Filesystem inspector could not exit. Keep the drive connected; its claim and lock are retained.":@"Read-only filesystem inspection timed out or could not be verified.");
    }
    recordExit(@"inspector",exitStatus);inspectorPID=-1;
    NSDictionary *h=json(readProtected(log)); require(WIFEXITED(exitStatus)&&WEXITSTATUS(exitStatus)==0&&[h[@"readyForWrite"]isEqual:@YES],h[@"reason"]?:@"The filesystem health check refused writing.");
    inspectedSerial(h);
    return h;
}
typedef NS_ENUM(NSUInteger, KernelUse) { KernelMetadataOnly, KernelRequireLoaded, KernelActivateForMount };
typedef NSDictionary *(^HelperCommand)(NSString *,NSArray *,NSTimeInterval);

// Absence is actionable only after a complete, recognizable public module
// table. Never infer it from a substring miss, a failed query or partial output.
static BOOL kernelLoaded(NSDictionary *result) {
    require([result[@"exit"]isKindOfClass:NSNumber.class]&&[result[@"exit"]intValue]==0
            &&[result[@"text"]isKindOfClass:NSString.class],@"Cannot verify the loaded macFUSE kernel state.");
    NSRegularExpression *row=[NSRegularExpression regularExpressionWithPattern:
        @"^[ \\t]*([0-9]+)[ \\t]+[0-9]+[ \\t]+(?:0x)?[0-9a-fA-F]+[ \\t]+(?:0x)?[0-9a-fA-F]+[ \\t]+(?:0x)?[0-9a-fA-F]+[ \\t]+([^ \\t]+)[ \\t]+\\(([^() \\t]+)\\)[ \\t]+[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[ \\t]+<(?:[0-9]+(?:[ \\t]+[0-9]+)*)?>[ \\t]*$"
        options:0 error:nil];
    NSMutableSet *indexes=[NSMutableSet new];BOOL found=NO;
    for(NSString *raw in [result[@"text"]componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
        NSString *line=[raw stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
        if(line.length==0||[line isEqual:@"No variant specified, falling back to release"])continue;
        NSTextCheckingResult *match=[row firstMatchInString:line options:0 range:NSMakeRange(0,line.length)];
        require(match&&NSEqualRanges(match.range,NSMakeRange(0,line.length)),@"The loaded-module table was incomplete or unrecognized. No kernel was loaded.");
        NSString *index=[line substringWithRange:[match rangeAtIndex:1]];
        require(![indexes containsObject:index],@"The loaded-module table contained duplicate entries.");[indexes addObject:index];
        NSString *identifier=[line substringWithRange:[match rangeAtIndex:2]],*version=[line substringWithRange:[match rangeAtIndex:3]];
        if([identifier hasPrefix:@"io.macfuse.filesystems.macfuse"]||[identifier hasPrefix:@"com.github.osxfuse."]||[identifier hasPrefix:@"com.google.filesystems.fusefs"]) {
            require([identifier isEqual:KernelIdentifier]&&[version isEqual:@"5.4.0"]&&!found,
                    @"A different or ambiguous macFUSE kernel is loaded. Revalidation is required.");found=YES;
        }
    }
    require(indexes.count>0,@"The loaded-module query returned no recognizable entries. No kernel was loaded.");
    return found;
}
static NSDictionary *kernelReadiness(KernelUse use,HelperCommand command) {
    require(use==KernelMetadataOnly||use==KernelRequireLoaded||use==KernelActivateForMount,@"Unknown kernel readiness operation.");
    BOOL loaded=kernelLoaded(command(@"/usr/bin/kmutil",@[@"showloaded",@"--list-only"],15));
    BOOL attempted=NO;
    if(!loaded&&use==KernelActivateForMount) {
        require(getuid()==0&&geteuid()==0,@"Only the installed administrator helper may activate macFUSE.");
        attempted=YES;
        // This only asks macOS to load the fixed, verified extension. macOS
        // retains its approval policy; no authorization setting is modified.
        NSDictionary *result=command(@"/usr/bin/kmutil",@[@"load",@"-p",KernelPath],15);
        require([result[@"exit"]isKindOfClass:NSNumber.class]&&[result[@"exit"]intValue]==0,
                @"macOS could not activate the verified macFUSE extension. Check macFUSE approval in Privacy & Security; the drive was left unchanged.");
        loaded=kernelLoaded(command(@"/usr/bin/kmutil",@[@"showloaded",@"--list-only"],15));
    }
    require(loaded||use==KernelMetadataOnly,@"The verified macFUSE 5.4.0 kernel is still not loaded. The drive was left unchanged.");
    return @{@"kernelLoaded":@(loaded),@"kernelLoadAttempted":@(attempted)};
}
// Keep file verification and fresh selection checks ahead of activation. The
// supplied functions are fixed internal implementations, never client input.
static NSDictionary *verifiedBackend(KernelUse use,NSDictionary *(^verifyFiles)(void),void (^revalidateSelection)(void),HelperCommand command) {
    NSDictionary *manifest=verifyFiles();
    if(use==KernelActivateForMount){require(revalidateSelection!=nil,@"A current card selection is required.");revalidateSelection();}
    NSDictionary *kernel=kernelReadiness(use,command);
    if(use==KernelActivateForMount)revalidateSelection();
    return @{@"manifest":manifest,@"kernel":kernel};
}
static NSDictionary *installation(void) {
    char path[PROC_PIDPATHINFO_MAXSIZE]; require(proc_pidpath(getpid(),path,sizeof path)>0,@"Cannot identify the installed helper.");
    NSString *p=@(path); versionDir=p.stringByDeletingLastPathComponent.stringByDeletingLastPathComponent;
    require([versionDir.stringByDeletingLastPathComponent isEqual:join(Base,@"Versions")]&&versionDir.lastPathComponent.length==64,@"Install the protected helper from Nativol Setup first.");
    NSDictionary *manifest=json(readProtected(join(versionDir,@"manifest.json")));
    require([sha(join(versionDir,@"manifest.json"))isEqual:versionDir.lastPathComponent],@"Installed version identity differs.");
    for(NSString *file in @[@"bin/NativolHelper",@"bin/ntfs-3g",@"bin/nativol-ntfs-health",@"lib/libfuse.2.dylib"])
        require([sha(join(versionDir,file))isEqual:manifest[@"files"][file]],@"Installed helper or driver checksum differs.");
    require([sha(join(versionDir,@"lib/libfuse.2.dylib"))isEqual:@"7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42"],@"The private macFUSE runtime differs from the reviewed version.");
    for(NSString *file in @[@"bin/NativolHelper",@"bin/ntfs-3g",@"bin/nativol-ntfs-health"])
        require([run(@"/usr/bin/codesign",@[@"--verify",@"--strict",join(versionDir,file)],15)[@"exit"]intValue]==0,@"The local executable signature is invalid.");
    NSOperatingSystemVersion os=NSProcessInfo.processInfo.operatingSystemVersion;
#if !defined(__x86_64__)
    require(NO,@"Apple silicon support is paused in this Intel release.");
#endif
    require(os.majorVersion==15&&os.minorVersion==7&&os.patchVersion==9,@"This experimental backend is enabled only on the tested Intel macOS 15.7.9 host.");
    int arm=0,translated=0;size_t architectureSize=sizeof(int);
    if(sysctlbyname("hw.optional.arm64",&arm,&architectureSize,NULL,0)==0)require(arm==0,@"Native Apple Silicon hardware validation is pending.");
    architectureSize=sizeof(int);
    if(sysctlbyname("sysctl.proc_translated",&translated,&architectureSize,NULL,0)==0)require(translated==0,@"Rosetta execution does not qualify as Intel hardware validation.");
    NSString *framework=@"/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/MFMount";
    require([sha(framework)isEqual:@"e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6"],@"The macFUSE framework changed. Revalidation is required.");
    require([sha(@"/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/Frameworks/libswiftCompatibilitySpan.dylib")isEqual:@"25a7269c3e5ce5e7094ff3169acfb6eaf1770be882e50fd442cf38a2e825776b"],@"The macFUSE compatibility runtime changed.");
    require([sha(@"/Library/Filesystems/macfuse.fs/Contents/Resources/mount_macfuse")isEqual:@"36231a75f70f76faefbe5582e19972d724a238e0b5ad74827c353d7a79631088"],@"The macFUSE mount helper changed.");
    NSString *kext=KernelPath;
    NSDictionary *kernelInfo=[NSPropertyListSerialization propertyListWithData:readProtected(join(kext,@"Contents/Info.plist")) options:NSPropertyListImmutable format:nil error:nil];
    require([kernelInfo isKindOfClass:NSDictionary.class]&&[kernelInfo[@"CFBundleIdentifier"]isEqual:KernelIdentifier]
            &&[kernelInfo[@"CFBundleVersion"]isEqual:@"5.4.0"]&&[kernelInfo[@"CFBundleExecutable"]isEqual:@"macfuse"],
            @"The installed macFUSE kernel identity or version changed.");
    require([sha(join(kext,@"Contents/MacOS/macfuse"))isEqual:@"bc8bd6e656eb75dd0ebc437b73faf8fa70e7b977c092a88eacbafc1326f5fc78"],@"The macFUSE kernel changed. Revalidation is required.");
    NSDictionary *signedResult=run(@"/usr/bin/codesign",@[@"--verify",@"--strict",@"-R",@"=anchor apple generic and certificate leaf[subject.OU] = \"3T5GSNBU6W\" and identifier \"io.macfuse.filesystems.macfuse.23\"",kext],15);
    require([signedResult[@"exit"]intValue]==0,@"macFUSE signature verification failed.");
    return manifest;
}
static NSDictionary *backendForUse(KernelUse use,NSDictionary *request) {
    return verifiedBackend(use,^NSDictionary *{return installation();},request?^{checkRequest(request);}:nil,
                           ^NSDictionary *(NSString *exe,NSArray *args,NSTimeInterval timeout){return run(exe,args,timeout);});
}
static BOOL candidate(struct statfs *m,uid_t uid,BOOL ro) {
    return !strcmp(m->f_mntonname,mountPath.fileSystemRepresentation)&&!strcmp(m->f_mntfromname,"/dev/fd/3")&&!strcmp(m->f_fstypename,"macfuse")&&!(m->f_flags_ext&MNT_EXT_FSKIT)&&m->f_owner==uid&&((m->f_flags&MNT_RDONLY)!=0)==ro&&(m->f_flags&MNT_NOSUID)&&(m->f_flags&MNT_NODEV)&&(m->f_fsid.val[0]||m->f_fsid.val[1]);
}
static BOOL mountedNow(struct statfs *out) {
    struct statfs *table; int count=getmntinfo(&table,MNT_NOWAIT); require(count>0,@"Cannot inspect the mount table.");
    unsigned found=0; for(int i=0;i<count;i++) if(!strcmp(table[i].f_mntonname,mountPath.fileSystemRepresentation)) { *out=table[i]; found++; }
    require(found<=1,@"Ambiguous managed mount."); return found==1;
}
static void sameMount(void) {
    struct statfs current; require(hasMounted&&mountedNow(&current)&&candidate(&current,[state[@"ownerUID"]unsignedIntValue],[state[@"mode"]isEqual:@"ro"])&&memcmp(&current.f_fsid,&ownedMount.f_fsid,sizeof(fsid_t))==0,@"The managed mount changed. No disk operation was performed.");
}
static BOOL ownedMountAbsentFromSnapshot(const struct statfs *table,int count) {
    require(hasMounted&&driverEverStarted&&(ownedMount.f_fsid.val[0]||ownedMount.f_fsid.val[1]),@"No established mount is available for passive completion.");
    require(table!=NULL&&count>0,@"Cannot verify whether the managed filesystem has disappeared.");
    unsigned paths=0,identities=0;const struct statfs *atPath=NULL;
    for(int i=0;i<count;i++) {
        if(!strcmp(table[i].f_mntonname,mountPath.fileSystemRepresentation)){paths++;atPath=&table[i];}
        if(!memcmp(&table[i].f_fsid,&ownedMount.f_fsid,sizeof(fsid_t)))identities++;
    }
    require(paths<=1&&identities<=1,@"The managed filesystem has an ambiguous mount identity.");
    if(paths) {
        struct statfs current=*atPath;
        require(identities==1&&!memcmp(&current.f_fsid,&ownedMount.f_fsid,sizeof(fsid_t))
            &&candidate(&current,[state[@"ownerUID"]unsignedIntValue],[state[@"mode"]isEqual:@"ro"]),
            @"The managed mount changed. No disk operation was performed.");
        return NO;
    }
    require(identities==0,@"The managed filesystem remains mounted at another path. Its session is retained.");
    return YES;
}
typedef NS_ENUM(int,ExternalMountProgress) { ExternalMountPresent,ExternalMountDraining,ExternalMountFinished };
static ExternalMountProgress pollExternalUnmount(void) {
    struct statfs *table=NULL;int count=getmntinfo(&table,MNT_NOWAIT);
    if(!ownedMountAbsentFromSnapshot(table,count))return ExternalMountPresent;
    // Finder can normally unmount the FUSE filesystem independently of the
    // retained physical-device claim. Wait for our actual child, never a PID
    // lookup or a signal, before releasing any session resources.
    if(driverPID>0&&driverRunning()) {
        if(![state[@"state"]isEqual:@"unmounting"]||![state[@"externalUnmountObserved"]boolValue]) {
            state[@"state"]=@"unmounting";state[@"externalUnmountObserved"]=@YES;
            state[@"message"]=@"The drive was unmounted outside Nativol. Waiting for the filesystem driver to finish…";atomicState();
        }
        return ExternalMountDraining;
    }
    require(driverPID<=0&&[state[@"driverExited"]boolValue]&&state[@"driverExit"]!=nil
        &&[state[@"driverExit"]intValue]==0&&state[@"driverSignal"]==nil
        &&inspectorPID<=0&&!arbitrationUncertain,
        @"The externally unmounted filesystem has not confirmed a clean driver exit. Its session is retained.");
    // Recheck the whole table after reaping; neither the old pathname nor its
    // FSID may have reappeared. Do not resolve, reopen, remount, or eject a BSD
    // name that could now belong to another attachment.
    table=NULL;count=getmntinfo(&table,MNT_NOWAIT);
    require(ownedMountAbsentFromSnapshot(table,count),@"The filesystem reappeared while its driver was finishing.");
    if(deviceFD>=0){close(deviceFD);deviceFD=-1;}
    if(claimed){DADiskUnclaim(claimedDisk);claimed=NO;}
    hasMounted=NO;state[@"externalUnmountObserved"]=@YES;state[@"state"]=@"external-unmounted";
    state[@"message"]=@"The filesystem was unmounted outside Nativol and its driver exited cleanly. This session has ended.";
    atomicState();return ExternalMountFinished;
}
static void requireFailedRestoreAbsent(BOOL callbackDone, const struct statfs *table, int count, NSString *source) {
    require(callbackDone,@"The read-only mount request is still pending. Keep the drive connected while its claim is retained.");
    require(table!=NULL&&count>0,@"Cannot verify the drive is unmounted after the read-only restore attempt.");
    NSString *rawSource=[@"/dev/r" stringByAppendingString:source.lastPathComponent];
    for(int i=0;i<count;i++)
        require(![source isEqual:@(table[i].f_mntfromname)]&&![rawSource isEqual:@(table[i].f_mntfromname)],
                @"A card mount remains after the failed read-only restore check. Its claim is retained for review.");
}
static BOOL failedSessionMayRestoreReadOnly(void) {
    return activeRequest!=nil&&originallyMountedRO&&wasUnmounted&&claimed&&claimedDisk!=NULL
        && !driverEverStarted&&driverPID<=0&&inspectorPID<=0&&!hasMounted&&!arbitrationUncertain;
}
static BOOL restoreReadOnly(NSDictionary *request) {
    require(claimed&&claimedDisk!=NULL&&driverPID<=0&&inspectorPID<=0&&!hasMounted&&!arbitrationUncertain,
            @"Read-only restoration requires the retained claim and no active driver, inspector, mount or pending disk operation.");
    checkIdentity(request);
    struct statfs *before=NULL;int beforeCount=getmntinfo(&before,MNT_NOWAIT);
    NSString *source=[@"/dev/" stringByAppendingString:request[@"bsdName"]];
    requireFailedRestoreAbsent(YES,before,beforeCount,source);
    if(deviceFD>=0){close(deviceFD);deviceFD=-1;}
    DAResult *restoreResult=calloc(1,sizeof *restoreResult);
    require(restoreResult!=NULL,@"Cannot prepare the read-only restore request.");
    state[@"restoreAttempted"]=@YES;
    @try {
        CFStringRef arguments[]={CFSTR("rdonly"),NULL};
        DADiskMountWithArguments(claimedDisk,NULL,kDADiskMountOptionDefault,complete,restoreResult,arguments);
        waitDA(restoreResult);checkIdentity(request);
        struct statfs *table=NULL;int count=getmntinfo(&table,MNT_NOWAIT);unsigned found=0;
        require(table!=NULL&&count>0,@"Cannot verify the restored read-only card mount.");
        for(int i=0;i<count;i++)if([source isEqual:@(table[i].f_mntfromname)]) {
            require(!strcmp(table[i].f_fstypename,"ntfs")&&(table[i].f_flags&MNT_RDONLY),@"Unexpected access after restoring the drive.");found++;
        }
        require(found==1,@"The drive's read-only Finder mount could not be uniquely verified.");
        state[@"restoredReadOnly"]=@YES;
    } @catch(NSException *e) {
        state[@"restoreMessage"]=e.reason?:@"Read-only remount was unavailable.";
        require(restoreResult->done,@"The read-only mount request is still pending. Keep the drive connected while its claim is retained.");
        checkIdentity(request);
        struct statfs *table=NULL;int count=getmntinfo(&table,MNT_NOWAIT);
        requireFailedRestoreAbsent(restoreResult->done,table,count,source);
    } @finally {
        // A timed-out callback still owns this context; keep it and the claim.
        if(restoreResult->done)free(restoreResult);
    }
    return [state[@"restoredReadOnly"]boolValue];
}
static void stopDriver(BOOL eject,NSDictionary *request) {
    sameMount(); state[@"state"]=@"unmounting"; state[@"message"]=@"Finishing writes and unmounting…"; atomicState();
    NSDictionary *result=run(@"/sbin/umount",@[mountPath],20);
    require([result[@"exit"]intValue]==0,@"The drive is busy. Close Finder windows and files, then retry Stop Writing.");
    struct statfs current; require(!mountedNow(&current),@"macOS has not completed the unmount.");
    hasMounted=NO;
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:30]; int status=0; pid_t done=0;
    while((done=waitpid(driverPID,&status,WNOHANG))==0&&deadline.timeIntervalSinceNow>0)CFRunLoopRunInMode(kCFRunLoopDefaultMode,.05,false);
    BOOL exited=done==driverPID;
    if(exited){recordExit(@"driver",status);driverPID=-1;}
    require(exited&&WIFEXITED(status)&&WEXITSTATUS(status)==0,@"The filesystem driver has not confirmed a clean exit. Keep the drive connected.");
    if(deviceFD>=0){close(deviceFD);deviceFD=-1;}
    NSDictionary *currentIdentity=checkIdentity(request);
    if(eject) {
        // The profile has exactly one partition; recheck its parent immediately
        // before requesting a normal whole-device eject.
        require([currentIdentity[@"partitionCount"]intValue]==1,@"Device topology changed before eject.");
        if(claimed){DADiskUnclaim(claimedDisk);claimed=NO;}
        DADiskRef whole=DADiskCopyWholeDisk(claimedDisk); DAResult *r=calloc(1,sizeof *r);
        DADiskEject(whole,kDADiskEjectOptionDefault,complete,r); waitDA(r); free(r); CFRelease(whole);
    } else {
        // Restore the drive's familiar Finder location using Apple's read-only
        // NTFS mount. No repair, force option, or writable fallback is allowed.
        restoreReadOnly(request);
        if(claimed){DADiskUnclaim(claimedDisk);claimed=NO;}
    }
    rmdir(mountPath.fileSystemRepresentation);
    state[@"state"]=eject?@"ejected":@"unmounted"; state[@"message"]=eject?@"Safe to disconnect the drive.":([state[@"restoredReadOnly"]boolValue]?@"Writes finished. The drive is back in Finder with read-only access.":@"Writes finished. The drive is unmounted; Disk Utility can restore read-only access.");state[@"driverExit"]=@0;atomicState();
}
static void supervise(NSDictionary *request,BOOL ro) {
    checkRequest(request); NSDictionary *backend=backendForUse(KernelActivateForMount,request),*manifest=backend[@"manifest"];
    activeRequest=[request copy];
    operation=uuid(request[@"operationId"]); privateDir=join(join(Private,@"sessions"),operation); statePath=join(join(Base,@"State"),[operation stringByAppendingString:@".json"]);
    protectedPath(join(Private,@"sessions"),YES); protectedPath(join(Base,@"State"),YES);
    require(mkdir(privateDir.fileSystemRepresentation,0700)==0,@"This operation was already used. Select the drive again.");
    NSString *lockName=[NSString stringWithFormat:@"media-%@-%@.lock",bootID(),request[@"mediaRegistryID"]];
    lockFD=open(join(Private,lockName).fileSystemRepresentation,O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
    struct stat lockStat={0};
    require(lockFD>=0&&fstat(lockFD,&lockStat)==0&&S_ISREG(lockStat.st_mode)&&lockStat.st_uid==0
            &&lockStat.st_nlink==1&&(lockStat.st_mode&0777)==0600&&flock(lockFD,LOCK_EX|LOCK_NB)==0,@"Another session owns this physical drive, or its lock cannot be verified.");
    mountPath=join(@"/Volumes",[@"Nativol-" stringByAppendingString:operation]);
    state=[@{@"schemaVersion":@1,@"operationId":operation,@"bootSessionUUID":bootID(),@"state":@"preparing",@"mode":ro?@"ro":@"rw",@"ownerUID":request[@"requestedUID"],@"mountPath":mountPath,@"source":@"/dev/fd/3",@"filesystem":@"macfuse",@"helperPID":@(getpid()),@"driverExecutable":join(versionDir,@"bin/ntfs-3g"),@"driverSHA256":manifest[@"files"][@"bin/ntfs-3g"],@"helperVersionDir":versionDir,@"targetProfile":@"intel-external-test-v1",@"bsdName":request[@"bsdName"],@"parentBSDName":request[@"parentBSDName"],@"partitionRegistryID":[request[@"partitionRegistryID"]stringValue],@"mediaRegistryID":[request[@"mediaRegistryID"]stringValue],@"message":@"Checking the selected drive…"}mutableCopy];
    [state addEntriesFromDictionary:backend[@"kernel"]];atomicState();
    claimAndUnmount(request); state[@"message"]=@"Checking NTFS health without modifying it…";atomicState();
    deviceFD=openCard(request,!ro);
    NSDictionary *inspection=health(request); state[@"health"]=inspection; if(inspection[@"volumeSerial"])state[@"volumeSerial"]=inspection[@"volumeSerial"];
    checkIdentity(request);
    protectedPath(@"/Volumes",YES); require(mkdir(mountPath.fileSystemRepresentation,0700)==0,@"The new mount directory is occupied.");
    require(chown(mountPath.fileSystemRepresentation,[request[@"requestedUID"]unsignedIntValue],[request[@"requestedGID"]unsignedIntValue])==0,@"Cannot prepare the mount owner.");
    // Darwin uses open xattr names. Without this explicit mode, NTFS-3G
    // rejects Finder metadata queries with EOPNOTSUPP (Finder error 100102).
    NSString *options=[NSString stringWithFormat:@"backend=kernel,local,no_def_opts,norecover,%@,no_detach,quiet,nosuid,nodev,default_permissions,uid=%@,gid=%@,umask=077,usermapping=/dev/null,windows_names,streams_interface=openxattr,volname=Nativol-%@",ro?@"ro":@"rw",request[@"requestedUID"],request[@"requestedGID"],request[@"bsdName"]];
    driverPID=spawnUser(join(versionDir,@"bin/ntfs-3g"),@[@"/dev/fd/3",mountPath,@"-o",options],deviceFD,join(privateDir,@"driver.log"),[request[@"requestedUID"]unsignedIntValue],[request[@"requestedGID"]unsignedIntValue]);
    driverEverStarted=YES;
    state[@"driverPID"]=@(driverPID); atomicState();
    NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:25]; struct statfs m;
    while(deadline.timeIntervalSinceNow>0) {
        require(driverRunning(),@"The driver could not mount the drive. The diagnostic log was retained.");
        if(mountedNow(&m)) { require(candidate(&m,[request[@"requestedUID"]unsignedIntValue],ro),@"The mount does not match the requested driver, owner or access mode."); ownedMount=m;hasMounted=YES;break; }
        checkIdentity(request); CFRunLoopRunInMode(kCFRunLoopDefaultMode,.1,false);
    }
    require(hasMounted,@"The driver mount timed out. Keep the drive connected while its state is investigated.");
    checkIdentity(request); state[@"fsid"]=@[@(m.f_fsid.val[0]),@(m.f_fsid.val[1])]; state[@"state"]=@"mounted";state[@"message"]=ro?@"Mounted read-only for verification.":@"Writing is enabled for this NTFS drive.";atomicState();
    for(;;) {
        @autoreleasepool {
            ExternalMountProgress external=pollExternalUnmount();
            if(external==ExternalMountFinished)return;
            if(external==ExternalMountDraining){CFRunLoopRunInMode(kCFRunLoopDefaultMode,.25,false);continue;}
            NSString *control=join(privateDir,@"control"); struct stat st;
            if(lstat(control.fileSystemRepresentation,&st)==0) {
                NSString *verb=[[NSString alloc]initWithData:readProtected(control) encoding:NSUTF8StringEncoding]; unlink(control.fileSystemRepresentation);
                @try { require([verb isEqual:@"unmount"]||[verb isEqual:@"eject"],@"Invalid session command."); stopDriver([verb isEqual:@"eject"],request); return; }
                @catch(NSException *e) { state[@"message"]=e.reason;state[@"state"]=hasMounted?@"mounted":@"attention";atomicState(); if(!hasMounted) { for(;;)CFRunLoopRunInMode(kCFRunLoopDefaultMode,1,false); } }
            }
            // Kernel object identity is checked throughout the attachment. A new
            // disk reusing the BSD name can never be silently enrolled.
            checkIdentity(request); sameMount();
            require(driverRunning(),@"The filesystem driver exited unexpectedly. Keep the drive connected.");
            CFRunLoopRunInMode(kCFRunLoopDefaultMode,.25,false);
        }
    }
}
static void control(NSString *identifier,NSString *verb) {
    backendForUse(KernelRequireLoaded,nil); NSString *op=uuid(identifier); NSString *directory=join(join(Private,@"sessions"),op); protectedPath(directory,YES);
    NSDictionary *s=json(readProtected(join(join(Base,@"State"),[op stringByAppendingString:@".json"])));
    require([s[@"bootSessionUUID"]isEqual:bootID()]&&[s[@"state"]isEqual:@"mounted"],@"This session is not currently mounted.");
    pid_t pid=[s[@"helperPID"]intValue]; char p[PROC_PIDPATHINFO_MAXSIZE]; struct proc_bsdinfo info={0};
    require(pid>0&&proc_pidpath(pid,p,sizeof p)>0&&[@(p)isEqual:join(s[@"helperVersionDir"],@"bin/NativolHelper")]&&proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof info)==sizeof info&&info.pbi_uid==0,@"The owning helper session is unavailable.");
    NSString *path=join(directory,@"control"); int fd=open(path.fileSystemRepresentation,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);require(fd>=0,@"A session command is already pending.");
    NSData *d=[verb dataUsingEncoding:NSUTF8StringEncoding]; BOOL ok=write(fd,d.bytes,d.length)==(ssize_t)d.length&&fsync(fd)==0;close(fd);require(ok,@"Cannot submit the session command.");
}
int main(int argc,const char **argv) {
    @autoreleasepool {
        umask(077);
        @try {
            require(argc==3,@"Usage: NativolHelper prepare <partition> | mount-rw|mount-ro <base64-request> | unmount|eject <operation-UUID>");
            session=DASessionCreate(NULL);require(session!=NULL,@"Disk Arbitration is unavailable.");DASessionScheduleWithRunLoop(session,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);
            NSString *verb=@(argv[1]);
            if([verb isEqual:@"prepare"]) { NSData *d=jsonData(prepare(@(argv[2]))); fwrite(d.bytes,1,d.length,stdout);fputc('\n',stdout);return 0; }
            if([verb isEqual:@"verify-installation"]&&strcmp(argv[2],"-")==0) {
                NSDictionary *backend=backendForUse(KernelMetadataOnly,nil);
                NSMutableDictionary *report=[backend[@"kernel"]mutableCopy];report[@"installationVerified"]=@YES;
                NSData *d=jsonData(report);fwrite(d.bytes,1,d.length,stdout);fputc('\n',stdout);return 0;
            }
            require(getuid()==0&&geteuid()==0,@"This action needs the normal macOS administrator prompt.");
            if([verb isEqual:@"unmount"]||[verb isEqual:@"eject"]) { control(@(argv[2]),verb);return 0; }
            require([verb isEqual:@"mount-rw"]||[verb isEqual:@"mount-ro"],@"Unknown helper action.");
            NSData *d=[[NSData alloc]initWithBase64EncodedString:@(argv[2]) options:0];supervise(json(d),[verb isEqual:@"mount-ro"]);return 0;
        } @catch(NSException *e) {
            if(inspectorPID>0)stopInspector();
            BOOL attention=hasMounted||driverPID>0||inspectorPID>0||arbitrationUncertain;
            if(mountPath) { @try { struct statfs current;attention=attention||mountedNow(&current); }
                @catch(NSException *unknown) { attention=YES; } }
            if(!attention&&failedSessionMayRestoreReadOnly()) {
                @try { restoreReadOnly(activeRequest); }
                @catch(NSException *restoreError) {
                    attention=YES;state[@"restoreMessage"]=restoreError.reason?:@"Read-only restoration could not be verified; the drive claim is retained.";
                }
            }
            if(state) { state[@"state"]=attention?@"attention":@"failed";state[@"message"]=e.reason?:@"Operation refused.";@try{atomicState();}@catch(NSException *ignored){} }
            fprintf(stderr,"Nativol: %s\n",e.reason.UTF8String);
            // Never kill a driver that might still be mounted or flushing. Keep
            // its claim and descriptor alive for diagnosis in uncertain states.
            if(attention)for(;;) {
                // A late external unmount can follow an identity/mount error.
                // Retry only passive completion; uncertain or failed child
                // states keep every resource and never trigger a disk action.
                @try{if(hasMounted&&pollExternalUnmount()==ExternalMountFinished)return 0;}
                @catch(NSException *retained){}
                CFRunLoopRunInMode(kCFRunLoopDefaultMode,1,false);
            }
            if(deviceFD>=0)close(deviceFD);if(claimed)DADiskUnclaim(claimedDisk);return 1;
        }
    }
}
