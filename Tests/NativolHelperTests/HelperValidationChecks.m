// Pure request/mount checks and mocked child lifecycle. Never invokes helper main,
// opens a device, runs an engine, mounts, unmounts, or requests authorization.
#define main nativol_helper_main_unused
#define waitpid nativol_test_waitpid
#define kill nativol_test_kill
#define getmntinfo nativol_test_getmntinfo
#define DADiskUnclaim nativol_test_unclaim
#define getuid nativol_test_getuid
#define geteuid nativol_test_geteuid
#include "../../Sources/NativolHelper/main.m"
#undef main
#undef waitpid
#undef kill
#undef getmntinfo
#undef DADiskUnclaim
#undef getuid
#undef geteuid

static NSMutableArray<NSNumber *> *waitAnswers, *signals;
static int childStatus;
static struct statfs mountSnapshot[4],laterMountSnapshot[4];
static int mountSnapshotCount,laterMountSnapshotCount,mountSnapshotCalls,unclaimCount;
static BOOL replaceMountSnapshot;
static uid_t kernelTestUID=501,kernelTestEUID=501;
uid_t nativol_test_getuid(void){return kernelTestUID;}
uid_t nativol_test_geteuid(void){return kernelTestEUID;}
int nativol_test_getmntinfo(struct statfs **out,int flags) {
    BOOL later=replaceMountSnapshot&&mountSnapshotCalls>0;mountSnapshotCalls++;
    *out=later?laterMountSnapshot:mountSnapshot;return later?laterMountSnapshotCount:mountSnapshotCount;
}
void nativol_test_unclaim(DADiskRef disk) { unclaimCount++; }
pid_t nativol_test_waitpid(pid_t pid, int *status, int options) {
    *status=childStatus;
    long long answer=waitAnswers.count?[waitAnswers.firstObject longLongValue]:0;
    if(waitAnswers.count)[waitAnswers removeObjectAtIndex:0];
    if(answer<0)errno=ECHILD;
    return answer>0?pid:(pid_t)answer;
}
int nativol_test_kill(pid_t pid,int signal) { [signals addObject:@(signal)];return 0; }

static unsigned checks=0;
static void verify(BOOL ok,NSString *message) {
    checks++; if(!ok){fprintf(stderr,"FAIL %s\n",message.UTF8String);exit(1);}
}
static NSMutableDictionary *validRequest(void) {
    return [@{@"schemaVersion":@1,@"operationId":@"10000000-0000-0000-0000-000000000001",
        @"bootSessionUUID":@"20000000-0000-0000-0000-000000000001",@"sessionID":@"30000000-0000-0000-0000-000000000001",
        @"createdAt":@"2026-10-06T00:00:00Z",@"expiresAt":@"2026-10-06T00:02:00Z",
        @"bsdName":@"disk2s1",@"parentBSDName":@"disk2",@"partitionRegistryID":@4294968775ULL,
        @"mediaRegistryID":@4294968768ULL,@"partitionOffsetBytes":@4194304,@"capacityBytes":@4004511744LL,
        @"mediaCapacityBytes":@4008706048LL,@"blockSizeBytes":@512,@"parentBlockSizeBytes":@512,
        @"requestedUID":@501,@"requestedGID":@20,@"deviceProtocol":@"USB",@"partitionMap":@"FDisk_partition_scheme",
        @"parentRelationVerified":@YES,@"partitionCount":@1,@"protectedVolume":@NO} mutableCopy];
}
static BOOL rejected(id request) {
    @try { validateRequestShape(request);return NO; }
    @catch(NSException *e) { return YES; }
}
static BOOL profileRejected(NSDictionary *value) {
    @try { profile(value);return NO; } @catch(NSException *e) { return YES; }
}
static void checkExternalProfiles(void) {
    NSMutableDictionary *value=validRequest();
    [value addEntriesFromDictionary:@{@"internal":@NO,@"parentInternal":@NO,@"whole":@NO,@"parentWhole":@YES,@"writableMedia":@YES,@"filesystem":@"ntfs"}];
    verify(!profileRejected(value),@"External USB MBR single-partition drive is eligible");
    NSMutableDictionary *large=[value mutableCopy];
    [large addEntriesFromDictionary:@{@"deviceProtocol":@"Thunderbolt",@"partitionMap":@"GUID_partition_scheme",@"partitionOffsetBytes":@1048576,@"capacityBytes":@1999996846080LL,@"mediaCapacityBytes":@2000000000000LL,@"blockSizeBytes":@4096,@"parentBlockSizeBytes":@4096}];
    verify(!profileRejected(large),@"Generic large external GPT drive with aligned 4K sectors is eligible");
    NSDictionary *bad=@{@"internal":@[@YES,NSNull.null],@"parentInternal":@[@YES],@"whole":@[@YES],@"parentWhole":@[@NO],@"writableMedia":@[@NO],@"filesystem":@[@"apfs"],@"deviceProtocol":@[@"Disk Image",@"PCI-Express"],@"partitionMap":@[@"Apple_partition_scheme"],@"partitionCount":@[@0,@2],@"parentRelationVerified":@[@NO],@"partitionOffsetBytes":@[@0,@(-1),@513,@(INT64_MAX)],@"capacityBytes":@[@0,@(-1),@(INT64_MAX)],@"blockSizeBytes":@[@1024,@4096],@"parentBlockSizeBytes":@[@4096],@"mediaCapacityBytes":@[@4096,@4008706049LL]};
    for(NSString *key in bad)for(id badValue in bad[key]) {
        NSMutableDictionary *changed=[value mutableCopy];changed[key]=badValue;
        verify(profileRejected(changed),[NSString stringWithFormat:@"Unsafe geometry/topology refused: %@",key]);
    }
    for(NSString *key in @[@"internal",@"parentInternal",@"whole",@"parentWhole",@"writableMedia",@"capacityBytes",@"mediaRegistryID"]) {
        NSMutableDictionary *changed=[value mutableCopy];[changed removeObjectForKey:key];
        verify(profileRejected(changed),@"Incomplete external profile fails closed");
    }
}
static void resetChildren(void) {
    state=[NSMutableDictionary new]; waitAnswers=[NSMutableArray new];signals=[NSMutableArray new];
    childStatus=0;inspectorPID=-1;driverPID=-1;
}
static void restorableFailure(void) {
    activeRequest=validRequest();originallyMountedRO=YES;wasUnmounted=YES;claimed=YES;
    claimedDisk=(DADiskRef)(uintptr_t)1;driverEverStarted=NO;driverPID=-1;inspectorPID=-1;
    hasMounted=NO;arbitrationUncertain=NO;
}
static void externalSession(struct statfs mount,struct statfs host) {
    if(deviceFD>=0)close(deviceFD);
    resetChildren();driverPID=23456;driverEverStarted=YES;hasMounted=YES;
    claimed=YES;claimedDisk=(DADiskRef)(uintptr_t)1;arbitrationUncertain=NO;ownedMount=mount;
    deviceFD=dup(STDERR_FILENO);verify(deviceFD>=0,@"Test session owns only a harmless duplicate descriptor");
    state[@"ownerUID"]=@501;state[@"mode"]=@"rw";state[@"state"]=@"mounted";
    mountSnapshot[0]=host;mountSnapshotCount=1;laterMountSnapshotCount=0;mountSnapshotCalls=0;
    replaceMountSnapshot=NO;unclaimCount=0;
}
static BOOL externalCompletionRefused(void) {
    @try{pollExternalUnmount();return NO;}@catch(NSException *e){return YES;}
}
static NSDictionary *kernelResult(NSString *text,int code){return @{@"exit":@(code),@"text":text};}
static void checkKernelActivation(void) {
    NSString *apple=@"1 150 0 0 0 com.apple.kpi.bsd (24.6.0) F6A1B3D4-1EBB-3628-A1C5-89506C51DDEC <>\n";
    NSString *fuse=@"210 0 0xffffff7f82000000 0x1000 0x1000 io.macfuse.filesystems.macfuse.23 (5.4.0) F6A1B3D4-1EBB-3628-A1C5-89506C51DDEC <9 8 7 6 3 1>\n";
    NSString *loaded=[apple stringByAppendingString:fuse];
    verify(!kernelLoaded(kernelResult(apple,0)),@"A full recognizable module table can establish macFUSE absence");
    verify(kernelLoaded(kernelResult([@"No variant specified, falling back to release\n" stringByAppendingString:loaded],0)),@"Exact identifier and version must be recognized in a complete row");
    NSArray *unknown=@[@"",@"No variant specified, falling back to release\n",@"io.macfuse.filesystems.macfuse.23 (5.4.0)",
        [apple stringByAppendingString:@"warning: incomplete table\n"],
        [apple stringByAppendingString:@"210 0 0 0 0 io.macfuse.filesystems.macfuse.23 (5.4.0)\n"],
        [loaded stringByAppendingString:fuse],
        [loaded stringByReplacingOccurrencesOfString:@"(5.4.0)" withString:@"(5.4.1)"],
        [loaded stringByReplacingOccurrencesOfString:@"macfuse.23" withString:@"macfuse.24"],
        [loaded stringByReplacingOccurrencesOfString:@"macfuse.23" withString:@"macfuse.23.other"],
        [loaded stringByReplacingOccurrencesOfString:@"io.macfuse.filesystems.macfuse.23" withString:@"com.github.osxfuse.filesystems.osxfuse"]];
    for(NSString *text in unknown){BOOL refused=NO;@try{kernelLoaded(kernelResult(text,0));}@catch(NSException *e){refused=YES;}
        verify(refused,@"Empty, substring-only, malformed, duplicate or changed FUSE module tables must fail closed");}
    for(KernelUse use=KernelMetadataOnly;use<=KernelActivateForMount;use++) {
        __block NSUInteger calls=0;
        NSDictionary *result=kernelReadiness(use,^NSDictionary *(NSString *exe,NSArray *args,NSTimeInterval timeout){
            calls++;verify([exe isEqual:@"/usr/bin/kmutil"]&&[args isEqual:@[@"showloaded",@"--list-only"]]&&timeout==15,@"Already-loaded paths may issue only the fixed bounded metadata query");return kernelResult(loaded,0);
        });
        verify(calls==1&&[result[@"kernelLoaded"]boolValue]&&![result[@"kernelLoadAttempted"]boolValue],@"Already-loaded kernels must never receive another load in any operation mode");
    }
    for(KernelUse use=KernelMetadataOnly;use<=KernelRequireLoaded;use++) {
        __block NSUInteger calls=0;BOOL refused=NO;NSDictionary *result=nil;
        @try{result=kernelReadiness(use,^NSDictionary *(NSString *exe,NSArray *args,NSTimeInterval timeout){
            calls++;verify([args isEqual:@[@"showloaded",@"--list-only"]],@"Installer verification and control actions must remain non-loading");return kernelResult(apple,0);
        });}@catch(NSException *e){refused=YES;}
        verify(calls==1&&refused==(use==KernelRequireLoaded),@"Metadata verification reports absence, while control refuses it without loading");
        if(result)verify(![result[@"kernelLoaded"]boolValue]&&![result[@"kernelLoadAttempted"]boolValue],@"Installation verification must not imply absent kernel approval or activation");
    }
    kernelTestUID=0;kernelTestEUID=0;
    for(NSUInteger scenario=0;scenario<12;scenario++) {
        __block NSMutableArray *events=[NSMutableArray new];__block NSUInteger calls=0,selectionChecks=0;
        BOOL refused=NO;NSDictionary *backend=nil;
        @try{backend=verifiedBackend(KernelActivateForMount,^NSDictionary *{
            [events addObject:@"files"];
            require(scenario!=1,@"Changed pinned file or failed signature.");return @{@"files":@{}};
        },^{
            selectionChecks++;[events addObject:@"selection"];
            require(scenario!=2&&!(scenario==11&&selectionChecks==2),@"Attachment changed or lease expired.");
        },^NSDictionary *(NSString *exe,NSArray *args,NSTimeInterval timeout){
            calls++;
            verify([exe isEqual:@"/usr/bin/kmutil"]&&timeout==15,@"Activation command executable and timeout must remain fixed");
            if(calls==2){
                [events addObject:@"load"];
                verify([args isEqual:@[@"load",@"-p",@"/Library/Filesystems/macfuse.fs/Contents/Extensions/14/macfuse.kext"]],@"Activation must use only the protected pinned kernel path and fixed arguments");
                if(scenario==6)@throw [NSException exceptionWithName:@"TestTimeout" reason:@"Activation timed out" userInfo:nil];
                return kernelResult(@"",scenario==5?27:0);
            }
            [events addObject:@"query"];
            verify([args isEqual:@[@"showloaded",@"--list-only"]],@"Pre- and post-activation must use the same bounded public metadata query");
            if(calls==1){
                if(scenario==3)return kernelResult(@"unknown table",0);
                if(scenario==4)return kernelResult(loaded,1);
                if(scenario==10)return kernelResult([loaded stringByReplacingOccurrencesOfString:@"(5.4.0)" withString:@"(5.4.1)"],0);
                return kernelResult(apple,0);
            }
            if(scenario==7)return kernelResult(apple,0);
            if(scenario==8)return kernelResult(@"unknown table",0);
            if(scenario==9)return kernelResult(loaded,1);
            return kernelResult(loaded,0);
        });}@catch(NSException *e){refused=YES;}
        verify(refused==(scenario!=0),@"Only verified files, current selection, successful activation and exact post-query may proceed");
        if(scenario==0){
            verify([events isEqual:@[@"files",@"selection",@"query",@"load",@"query",@"selection"]],@"File and fresh selection validation must precede load, with selection checked again afterwards");
            verify([backend[@"kernel"][@"kernelLoaded"]boolValue]&&[backend[@"kernel"][@"kernelLoadAttempted"]boolValue],@"Successful post-reboot activation records both load attempt and observed readiness");
        } else if(scenario<=2)verify(calls==0,@"Changed files or stale selection must prevent even the loaded-state query");
        else if(scenario==3||scenario==4||scenario==10)verify(calls==1&&![events containsObject:@"load"],@"Unknown query, query failure or different loaded version must never trigger activation");
        else if(scenario==5||scenario==6)verify(calls==2,@"Rejected or timed-out activation must not continue to a driver or retry");
        else verify(calls==3,@"A successful load exit is insufficient without fresh exact kernel and selection verification");
    }
    kernelTestUID=501;kernelTestEUID=501;
    __block NSUInteger calls=0;BOOL refused=NO;
    @try{kernelReadiness(KernelActivateForMount,^NSDictionary *(NSString *exe,NSArray *args,NSTimeInterval timeout){calls++;return kernelResult(apple,0);});}@catch(NSException *e){refused=YES;}
    verify(refused&&calls==1,@"An ordinary user cannot activate the kernel even when verified absent");
}

int main(void) {
    @autoreleasepool {
        checkKernelActivation();
        checkExternalProfiles();
        @try { inspectedSerial(@{@"volumeSerial":@"abcdef0123456789"}); verify(YES,@"Inspected filesystem serial passes"); }
        @catch(NSException *e) { verify(NO,@"Inspected filesystem serial must pass"); }
        for(id wrong in @[@"0000000000000000",@"not-an-ntfs-id",@"abcdef0123456789\n",@42,NSNull.null]) {
            BOOL refused=NO; @try { inspectedSerial(@{@"volumeSerial":wrong}); } @catch(NSException *e) { refused=YES; }
            verify(refused,@"Invalid filesystem serial must be refused before a writable driver starts");
        }
        BOOL missingRefused=NO; @try { inspectedSerial(@{}); } @catch(NSException *e) { missingRefused=YES; }
        verify(missingRefused,@"Missing filesystem serial must be refused");
        NSMutableDictionary *base=validRequest();
        verify(!rejected(base),@"Normal numeric IDs, UID501 and GID20 must pass the pure schema");
        NSData *encoded=[NSJSONSerialization dataWithJSONObject:base options:0 error:nil];
        verify(!rejected([NSJSONSerialization JSONObjectWithData:encoded options:0 error:nil]),@"Actual JSON round-trip must preserve valid integer/boolean fields");
        for(id invalid in @[@[],NSNull.null,@"request",@{}])verify(rejected(invalid),@"Malformed top-level request must be refused");
        for(NSString *key in base.allKeys) {
            NSMutableDictionary *r=[base mutableCopy];[r removeObjectForKey:key];verify(rejected(r),@"Missing mandatory field must fail");
            r=[base mutableCopy];r[key]=NSNull.null;verify(rejected(r),@"Null mandatory field must fail");
        }
        NSDictionary *invalids=@{
            @"schemaVersion":@[@YES,@0,@2,@1.0,@"1"],
            @"requestedUID":@[@0,@500,@4294967797ULL,@(-1),@501.5,@"501",@YES],
            @"requestedGID":@[@0,@4294967316ULL,@(-1),@20.1,@"20"],
            @"partitionRegistryID":@[@0,@(-1),@YES,@1001.5,@"1001"],
            @"partitionOffsetBytes":@[@(-1),@(UINT64_MAX),@0.5],
            @"capacityBytes":@[@0,@(-1),@(UINT64_MAX)],
            @"parentRelationVerified":@[@1,@0,@"true"],@"protectedVolume":@[@0,@1,@"false"],
            @"operationId":@[@"00000000-0000-0000-0000-000000000000",@"x",@17],
            @"bsdName":@[@"/dev/disk2s1",@"disk2s1;touch x",@"disk2",@"disk3s1",@"disk2s1\n"],
            @"parentBSDName":@[@"disk3",@"/dev/disk2",@"disk2\n"],
            @"expiresAt":@[@"2026-10-06T00:02:01Z",@"2026-10-06T00:00:00Z",@"not-a-date"],
            @"createdAt":@[@"2026-10-06T00:02:01Z",@"not-a-date"]};
        for(NSString *key in invalids)for(id value in invalids[key]){
            NSMutableDictionary *r=[base mutableCopy];r[key]=value;verify(rejected(r),[NSString stringWithFormat:@"Invalid %@ value must fail: %@",key,value]);
        }
        for(NSString *key in @[@"partitionUUID",@"mediaUUID"]){
            NSMutableDictionary *r=[base mutableCopy];r[key]=@"40000000-0000-0000-0000-000000000001";verify(!rejected(r),@"Optional valid UUID should pass");
            for(id invalid in @[NSNull.null,@"",@"00000000-0000-0000-0000-000000000000",@4]){r[key]=invalid;verify(rejected(r),@"Invalid optional UUID must fail");}
        }
        NSMutableDictionary *extra=[base mutableCopy];extra[@"command"]=@"arbitrary";verify(rejected(extra),@"Unknown operation/command fields must fail");

        mountPath=@"/Volumes/Nativol-Test";
        struct statfs good={0};strlcpy(good.f_mntonname,mountPath.UTF8String,sizeof good.f_mntonname);
        strlcpy(good.f_mntfromname,"/dev/fd/3",sizeof good.f_mntfromname);strlcpy(good.f_fstypename,"macfuse",sizeof good.f_fstypename);
        good.f_owner=501;good.f_flags=MNT_NOSUID|MNT_NODEV;good.f_fsid.val[0]=100;
        verify(candidate(&good,501,NO),@"Expected writable mount tuple should be accepted");
        struct statfs changed=good;changed.f_flags|=MNT_RDONLY;verify(!candidate(&changed,501,NO)&&candidate(&changed,501,YES),@"Read-only capability must match requested mode");
        changed=good;changed.f_flags&=~MNT_NOSUID;verify(!candidate(&changed,501,NO),@"Unsafe mount flags must fail");
        changed=good;changed.f_flags&=~MNT_NODEV;verify(!candidate(&changed,501,NO),@"Device-node permission must be disabled");
        changed=good;changed.f_owner=502;verify(!candidate(&changed,501,NO),@"Wrong mount owner must fail");
        changed=good;changed.f_fsid.val[0]=0;verify(!candidate(&changed,501,NO),@"Missing FSID must fail");
        changed=good;strlcpy(changed.f_mntfromname,"/dev/disk2s1",sizeof changed.f_mntfromname);verify(!candidate(&changed,501,NO),@"An unrelated native mount cannot become helper-owned");
        changed=good;strlcpy(changed.f_mntonname,"/Volumes/Other",sizeof changed.f_mntonname);verify(!candidate(&changed,501,NO),@"Wrong mount path must fail");

        struct statfs host={0};strlcpy(host.f_mntfromname,"/dev/disk1s1",sizeof host.f_mntfromname);
        BOOL restoreAccepted=YES;
        @try{requireFailedRestoreAbsent(YES,&host,1,@"/dev/disk2s1");}@catch(NSException *e){restoreAccepted=NO;}
        verify(restoreAccepted,@"A completed restore refusal with a fresh unrelated mount snapshot can report cleanly unmounted");
        for(int situation=0;situation<5;situation++){
            struct statfs remaining=host;
            if(situation==2)strlcpy(remaining.f_mntfromname,"/dev/disk2s1",sizeof remaining.f_mntfromname);
            if(situation==3)strlcpy(remaining.f_mntfromname,"/dev/rdisk2s1",sizeof remaining.f_mntfromname);
            if(situation==4){strlcpy(remaining.f_mntfromname,"/dev/disk2s1",sizeof remaining.f_mntfromname);remaining.f_flags=MNT_RDONLY;}
            BOOL refused=NO;
            @try{requireFailedRestoreAbsent(situation!=0,&remaining,situation==1?0:1,@"/dev/disk2s1");}
            @catch(NSException *e){refused=YES;}
            verify(refused,@"Pending callback, unknown scan, or any remaining selected mount must preserve attention");
        }

        for(int codeIndex=0;codeIndex<2;codeIndex++){
            int code=codeIndex?EACCES:EPERM;
            NSString *message=deviceOpenFailureMessage(code);
            verify([message containsString:@"Allow NativolHelper in Full Disk Access"]&&[message containsString:[NSString stringWithFormat:@"errno %d",code]],@"Device permission errors must identify the exact helper permission and preserve errno");
        }
        NSString *missing=deviceOpenFailureMessage(ENOENT);
        verify([missing containsString:@"errno 2"]&&![missing containsString:@"Full Disk Access"],@"Non-permission failures must retain their POSIX description without claiming FDA is the cause");
        restorableFailure();verify(failedSessionMayRestoreReadOnly(),@"An unchanged pre-driver failure after our own RO unmount may request guarded restoration");
        for(int condition=0;condition<10;condition++){
            restorableFailure();
            switch(condition){
                case 0:activeRequest=nil;break;
                case 1:originallyMountedRO=NO;break;
                case 2:wasUnmounted=NO;break;
                case 3:claimed=NO;break;
                case 4:claimedDisk=NULL;break;
                case 5:driverEverStarted=YES;break;
                case 6:driverPID=12345;break;
                case 7:inspectorPID=12345;break;
                case 8:hasMounted=YES;break;
                case 9:arbitrationUncertain=YES;break;
            }
            verify(!failedSessionMayRestoreReadOnly(),@"Missing prior RO ownership, any driver attempt, child, mount or pending arbitration must prevent automatic restoration");
        }
        activeRequest=nil;originallyMountedRO=NO;wasUnmounted=NO;claimed=NO;claimedDisk=NULL;
        driverEverStarted=NO;hasMounted=NO;arbitrationUncertain=NO;

        resetChildren();inspectorPID=12345;[waitAnswers addObjectsFromArray:@[@0,@1]];childStatus=SIGTERM;
        stopInspector();verify(inspectorPID==-1&&signals.count==1&&[signals[0]intValue]==SIGTERM,@"Terminated inspector must be reaped before its lease is released");
        verify([state[@"inspectorSignal"]intValue]==SIGTERM,@"Inspector termination must be reported accurately");
        resetChildren();inspectorPID=12345;[waitAnswers addObject:@1];stopInspector();
        verify(inspectorPID==-1&&signals.count==0,@"Already-exited inspector must not receive a signal by stale PID");
        resetChildren();inspectorPID=12345;[waitAnswers addObject:@(-1)];stopInspector();
        verify(inspectorPID==12345&&signals.count==0&&[state[@"inspectorStillRunning"]boolValue],@"Unknown child ownership must retain the session without signaling a PID");
        resetChildren();inspectorPID=12345;stopInspector();
        verify(inspectorPID==12345&&[signals isEqual:@[@(SIGTERM),@(SIGKILL)]]&&[state[@"inspectorStillRunning"]boolValue],@"Uninterruptible inspector must retain its session after bounded TERM/KILL waits");
        resetChildren();driverPID=23456;[waitAnswers addObject:@1];childStatus=7<<8;
        verify(!driverRunning()&&driverPID==-1&&[state[@"driverExit"]intValue]==7,@"Failed reaped driver must not remain a live stale PID");
        resetChildren();driverPID=23456;verify(driverRunning()&&driverPID==23456,@"Still-running owned driver must retain its PID");
        resetChildren();driverPID=23456;[waitAnswers addObject:@(-1)];BOOL refused=NO;
        @try{driverRunning();}@catch(NSException *e){refused=YES;}
        verify(refused&&driverPID==23456&&signals.count==0,@"Unknown driver process state must be retained without signaling");

        // Exercise real passive-completion control flow against mocked kernel
        // snapshots/child exits. Only the final state is written to a new local
        // temporary directory; no device, engine, or Disk Arbitration call runs.
        char stateDirectoryTemplate[]="/private/tmp/nativol-external-unmount-checks.XXXXXX";
        char *testDirectory=mkdtemp(stateDirectoryTemplate);verify(testDirectory!=NULL,@"Create isolated state directory");
        statePath=join(@(testDirectory),@"state.json");
        strlcpy(host.f_mntonname,"/",sizeof host.f_mntonname);host.f_fsid.val[0]=200;
        externalSession(good,host);int retainedFD=deviceFD;
        [waitAnswers addObjectsFromArray:@[@0,@1]];
        verify(pollExternalUnmount()==ExternalMountDraining&&driverPID==23456&&deviceFD==retainedFD&&claimed&&hasMounted&&unclaimCount==0,
            @"A live driver after external unmount must retain its descriptor and claim while draining");
        verify([state[@"state"]isEqual:@"unmounting"]&&[state[@"externalUnmountObserved"]boolValue]&&signals.count==0,
            @"External unmount drains passively without signaling the driver");
        verify(pollExternalUnmount()==ExternalMountFinished&&driverPID==-1&&deviceFD==-1&&!claimed&&!hasMounted&&unclaimCount==1,
            @"Clean child exit plus full-table absence releases only the departed session");
        verify(fcntl(retainedFD,F_GETFD)==-1&&errno==EBADF&&[state[@"state"]isEqual:@"external-unmounted"]&&[state[@"driverExited"]boolValue]&&[state[@"driverExit"]intValue]==0,
            @"Successful passive completion closes its descriptor and records external-unmounted, never ejected");
        NSDictionary *published=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:statePath] options:0 error:nil];
        verify([published[@"state"]isEqual:@"external-unmounted"],@"Terminal passive-completion state must be published");

        externalSession(good,host);state[@"state"]=@"attention";activeRequest=@{@"bsdName":@"disk999999s1"};
        [waitAnswers addObject:@1];
        verify(pollExternalUnmount()==ExternalMountFinished&&unclaimCount==1,
            @"Late clean completion must not depend on an old or reused BSD attachment identity");

        externalSession(good,host);mountSnapshot[0]=good;[waitAnswers addObject:@1];
        verify(pollExternalUnmount()==ExternalMountPresent&&driverPID==23456&&waitAnswers.count==1&&unclaimCount==0,
            @"A still-present owned filesystem cannot be completed or have its driver consumed by absence reconciliation");
        for(int scenario=0;scenario<6;scenario++) {
            externalSession(good,host);[waitAnswers addObject:@1];
            switch(scenario) {
                case 0:mountSnapshotCount=0;break;
                case 1:mountSnapshot[0]=good;strlcpy(mountSnapshot[0].f_mntonname,"/Volumes/Alias",sizeof mountSnapshot[0].f_mntonname);break;
                case 2:mountSnapshot[0]=good;mountSnapshot[0].f_fsid.val[0]++;break;
                case 3:mountSnapshot[0]=good;mountSnapshot[1]=good;mountSnapshotCount=2;break;
                case 4:mountSnapshot[0]=good;mountSnapshot[0].f_owner=502;break;
                case 5:mountSnapshot[0]=good;mountSnapshot[0].f_flags|=MNT_RDONLY;break;
            }
            verify(externalCompletionRefused()&&driverPID==23456&&deviceFD>=0&&claimed&&hasMounted&&unclaimCount==0&&waitAnswers.count==1,
                @"Unknown mount scan, FSID alias, replacement, duplicates, wrong owner or changed access must retain the session");
        }
        for(int scenario=0;scenario<5;scenario++) {
            externalSession(good,host);[waitAnswers addObject:scenario==2?@(-1):@1];
            if(scenario==0)childStatus=7<<8;
            if(scenario==1)childStatus=SIGTERM;
            if(scenario==3)inspectorPID=12345;
            if(scenario==4)arbitrationUncertain=YES;
            verify(externalCompletionRefused()&&deviceFD>=0&&claimed&&hasMounted&&unclaimCount==0&&signals.count==0,
                @"Nonzero, signaled, unknown child state or outstanding inspector/arbitration must prevent resource release");
        }
        externalSession(good,host);driverPID=-1;
        verify(externalCompletionRefused()&&deviceFD>=0&&claimed&&unclaimCount==0,
            @"A missing owned-child exit result cannot be interpreted as a clean exit");
        externalSession(good,host);[waitAnswers addObject:@1];replaceMountSnapshot=YES;
        laterMountSnapshot[0]=good;laterMountSnapshotCount=1;
        verify(externalCompletionRefused()&&driverPID==-1&&deviceFD>=0&&claimed&&hasMounted&&unclaimCount==0&&mountSnapshotCalls==2,
            @"A filesystem reappearing after child reap must fail closed using a fresh full-table snapshot");
        if(deviceFD>=0){close(deviceFD);deviceFD=-1;}
        claimed=NO;claimedDisk=NULL;hasMounted=NO;inspectorPID=-1;driverPID=-1;arbitrationUncertain=NO;activeRequest=nil;
        verify([[NSFileManager defaultManager]removeItemAtPath:@(testDirectory) error:nil],@"Remove only this test's temporary state directory");
        printf("%u helper validation assertions passed (no hardware or engine operations).\n",checks);
        return 0;
    }
}
