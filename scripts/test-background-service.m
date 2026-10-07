#define NATIVOL_SERVICE_TEST 1
#import "../Sources/NativolService/main.m"

static int checks;
static void expect(BOOL value,NSString *name) {
    if(!value){fprintf(stderr,"FAIL: %s\n",name.UTF8String);exit(1);}checks++;
}
static BOOL refused(void (^block)(void)) {
    @try {block();return NO;} @catch(NSException *exception){return [exception.name isEqual:@"NativolServiceRefusal"];}
}
static NSMutableDictionary *mountEnvelope(void) {
    return [@{@"schemaVersion":@1,@"verb":@"mount-rw",@"request":@{@"requestedUID":@501,@"requestedGID":@20,
        @"bootSessionUUID":@"11111111-1111-1111-1111-111111111111",@"operationId":@"22222222-2222-2222-2222-222222222222"}}mutableCopy];
}
int main(int argc,const char **argv) {
    (void)argv;
    @autoreleasepool {
        expect(argc==1 && getuid()!=0,@"ordinary-user test only");
        NSString *boot=@"11111111-1111-1111-1111-111111111111",*op=@"22222222-2222-2222-2222-222222222222";
        NSDictionary *valid=mountEnvelope();
        NSDictionary *selection=@{@"mediaRegistryID":@1001,@"partitionRegistryID":@1002};
        NSDictionary *other=@{@"bootSessionUUID":boot,@"state":@"mounted",@"mediaRegistryID":@"2001",@"partitionRegistryID":@"2002"};
        expect(!refused(^{admitMount(selection,@[],boot);}),@"first drive admitted");
        expect(!refused(^{admitMount(selection,@[other],boot);}),@"another independent drive admitted");
        for(NSString *phase in @[@"preparing",@"mounted",@"unmounting",@"attention"]) {
            NSMutableDictionary *s=[other mutableCopy];s[@"state"]=phase;
            expect(!refused(^{admitMount(selection,@[s],boot);}),@"other identified drive does not block admission");
            s[@"mediaRegistryID"]=@"1001";
            expect(refused(^{admitMount(selection,@[s],boot);}),@"same physical medium refused in every active state");
        }
        NSMutableDictionary *conflict=[other mutableCopy];conflict[@"partitionRegistryID"]=@"1002";
        expect(refused(^{admitMount(selection,@[conflict],boot);}),@"same partition refused");
        expect(refused(^{admitMount(selection,@[other,other],boot);}),@"ambiguous duplicate active identity refused");
        for(id invalid in @[@"",@"0",@"18446744073709551616",@"-1",@"2001\n",@2001,NSNull.null]) {
            NSMutableDictionary *s=[other mutableCopy];s[@"mediaRegistryID"]=invalid;
            expect(refused(^{admitMount(selection,@[s],boot);}),@"malformed active identity refused");
        }
        for(NSString *phase in @[@"failed",@"ejected",@"unmounted",@"external-unmounted"]) {
            NSMutableDictionary *s=[other mutableCopy];s[@"mediaRegistryID"]=@"1001";s[@"state"]=phase;
            expect(!refused(^{admitMount(selection,@[s],boot);}),@"completed session does not block admission");
        }
        NSMutableDictionary *old=[other mutableCopy];old[@"bootSessionUUID"]=op;old[@"mediaRegistryID"]=@"1001";
        expect(!refused(^{admitMount(selection,@[old],boot);}),@"old-boot session ignored");
        NSMutableArray *many=[NSMutableArray new];
        for(int i=0;i<8;i++)[many addObject:@{@"bootSessionUUID":boot,@"state":@"mounted",@"mediaRegistryID":[NSString stringWithFormat:@"%d",2000+i*2],@"partitionRegistryID":[NSString stringWithFormat:@"%d",2001+i*2]}];
        expect(refused(^{admitMount(selection,many,boot);}),@"ninth drive refused without exhausting control capacity");
        [many removeLastObject];expect(!refused(^{admitMount(selection,many,boot);}),@"eighth drive admitted");
        expect(!refused(^{validateEnvelope(valid,501,20,boot);}),@"valid mount envelope");
        expect(refused(^{validateEnvelope(valid,502,20,boot);}),@"cross-user mount refused");
        expect(refused(^{validateEnvelope(valid,501,80,boot);}),@"wrong primary gid refused");
        expect(refused(^{validateEnvelope(valid,501,20,op);}),@"stale boot refused");
        for(id uid in @[@YES,@(-1),@501.0,@"501",NSNull.null]) {
            NSMutableDictionary *bad=mountEnvelope(),*inner=[bad[@"request"]mutableCopy];inner[@"requestedUID"]=uid;bad[@"request"]=inner;
            expect(refused(^{validateEnvelope(bad,501,20,boot);}),@"malformed uid refused");
        }
        for(NSString *verb in @[@"mount",@"prepare",@"verify-installation",@"/bin/sh",@"mount-rw;touch /tmp/x"]) {
            NSMutableDictionary *bad=mountEnvelope();bad[@"verb"]=verb;
            expect(refused(^{validateEnvelope(bad,501,20,boot);}),@"only fixed verbs accepted");
        }
        NSMutableDictionary *bad=mountEnvelope();bad[@"options"]=@"force";
        expect(refused(^{validateEnvelope(bad,501,20,boot);}),@"caller options refused");
        for(NSString *verb in @[@"unmount",@"eject"]) {
            expect(!refused(^{validateEnvelope(@{@"schemaVersion":@1,@"verb":verb,@"operationId":op},501,20,boot);}),@"fixed control accepted");
            expect(refused(^{validateEnvelope(@{@"schemaVersion":@1,@"verb":verb,@"operationId":@"../../etc"},501,20,boot);}),@"control path traversal refused");
        }
        expect(!refused(^{validateEnvelope(@{@"schemaVersion":@1,@"verb":@"status"},501,20,boot);}),@"status envelope");
        expect(refused(^{validateEnvelope(@{@"schemaVersion":@YES,@"verb":@"status"},501,20,boot);}),@"boolean schema refused");
        NSString *version=[Base stringByAppendingPathComponent:@"Versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"];
        NSDictionary *state=@{@"schemaVersion":@1,@"ownerUID":@501,@"operationId":op,@"bootSessionUUID":boot,
            @"helperVersionDir":version,@"targetProfile":@"intel-external-test-v1",@"source":@"/dev/fd/3",@"filesystem":@"macfuse",
            @"mountPath":[@"/Volumes/Nativol-" stringByAppendingString:op]};
        expect(!refused(^{validateOwnedState(state,op,501,version,boot);}),@"owned protected-state identity");
        expect(refused(^{validateOwnedState(state,op,502,version,boot);}),@"cross-user control refused");
        expect(refused(^{validateOwnedState(state,op,501,version,op);}),@"old-boot control refused");
        expect(refused(^{validateOwnedState(state,op,501,@"/tmp/helper",boot);}),@"caller helper path refused");
        expect(refused(^{validateOwnedState(state,boot,501,version,boot);}),@"wrong operation control refused");
        for(NSString *key in @[@"source",@"filesystem",@"mountPath",@"targetProfile"]) {
            NSMutableDictionary *s=[state mutableCopy];s[key]=@"other";
            expect(refused(^{validateOwnedState(s,op,501,version,boot);}),@"mismatched state field refused");
        }
        NSData *hash=[NSMutableData dataWithLength:20];NSDictionary *config=@{@"clientCDHash":hex(hash)};
        NSMutableDictionary *info=[@{(__bridge NSString*)kSecCodeInfoFlags:@(kSecCodeSignatureRuntime),
            (__bridge NSString*)kSecCodeInfoStatus:@(kSecCodeStatusValid),(__bridge NSString*)kSecCodeInfoUnique:hash}mutableCopy];
        expect(!refused(^{hardenedIdentity(info,config);}),@"hardened exact code identity");
        for(NSNumber *status in @[@0,@(kSecCodeStatusValid|kSecCodeStatusDebugged)]) {
            NSMutableDictionary *s=[info mutableCopy];s[(__bridge NSString*)kSecCodeInfoStatus]=status;
            expect(refused(^{hardenedIdentity(s,config);}),@"invalid/debugged code refused");
        }
        NSMutableDictionary *plain=[info mutableCopy];plain[(__bridge NSString*)kSecCodeInfoFlags]=@0;
        expect(refused(^{hardenedIdentity(plain,config);}),@"unhardened code refused");
        for(id entitlements in @[@{@"com.apple.security.get-task-allow":@YES},@{@"com.apple.security.cs.disable-library-validation":@YES},@"invalid"]) {
            NSMutableDictionary *s=[info mutableCopy];s[(__bridge NSString*)kSecCodeInfoEntitlementsDict]=entitlements;
            expect(refused(^{hardenedIdentity(s,config);}),@"runtime exceptions refused");
        }
        expect(refused(^{hardenedIdentity(info,@{@"clientCDHash":@"other"});}),@"wrong dynamic code hash refused");
        for(uint32_t length=0;length<=65536;length+=65536) {
            int sockets[2];expect(socketpair(AF_UNIX,SOCK_STREAM,0,sockets)==0,@"socketpair");
            uint32_t size=htonl(length);expect(write(sockets[0],&size,4)==4,@"header transport");
            int receiver=sockets[1];
            expect(refused(^{receive(receiver);}),@"zero/oversized frame refused");close(sockets[0]);close(sockets[1]);
        }
        int pair[2];expect(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0,@"framed transport pair");
        respond(pair[0],@{@"ok":@YES,@"status":@"ready"});
        expect([receive(pair[1])isEqual:@{@"ok":@YES,@"status":@"ready"}],@"bounded JSON roundtrip");
        audit_token_t actualToken; socklen_t tokenLength=sizeof actualToken;
        expect(getsockopt(pair[0],SOL_LOCAL,LOCAL_PEERTOKEN,&actualToken,&tokenLength)==0
               && tokenLength==sizeof actualToken && audit_token_to_euid(actualToken)==geteuid()
               && audit_token_to_pid(actualToken)==getpid(),@"kernel peer audit token identifies actual caller");
        SecCodeRef actualCode=NULL;
        NSDictionary *guestAttributes=@{(__bridge NSString*)kSecGuestAttributeAudit:
            [NSData dataWithBytes:&actualToken length:sizeof actualToken]};
        expect(SecCodeCopyGuestWithAttributes(NULL,(__bridge CFDictionaryRef)guestAttributes,kSecCSDefaultFlags,&actualCode)==errSecSuccess
               && actualCode!=NULL,@"Security framework resolves kernel audit identity");
        if(actualCode)CFRelease(actualCode);
        // Real LOCAL_PEERTOKEN + dynamic Security.framework query, not a mock.
        int peer=pair[0];
        expect(refused(^{authenticate(peer,config);}),@"uninstalled test executable rejected by real peer authentication");
        close(pair[0]);close(pair[1]);
        expect(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0,@"partial transport pair");
        uint32_t promised=htonl(8);write(pair[0],&promised,4);write(pair[0],"{}",2);close(pair[0]);
        int receiver=pair[1];
        expect(refused(^{receive(receiver);}),@"truncated frame refused");close(pair[1]);
        printf("PASS: %d service policy, ownership, framing and real untrusted-peer checks; no daemon/device actions\n",checks);
        return 0;
    }
}
