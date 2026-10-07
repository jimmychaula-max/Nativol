#if NATIVOL_STANDALONE_CHECKS
import Darwin
import Foundation

@main
enum CoreCheckRunner {
    static func main() {
        let checks = CoreSafetyChecks().all + TestSessionChecks().all + ExperimentalWriteLeaseChecks().all + AutomaticMountPolicyChecks().all
        var failures = 0
        for (name, check) in checks {
            do {
                try check()
                print("PASS \(name)")
            } catch {
                failures += 1
                print("FAIL \(name): \(error)")
            }
        }
        print("\(checks.count - failures)/\(checks.count) core safety checks passed.")
        if failures != 0 { exit(1) }
    }
}
#endif
