# Charge policy POWER_GOOD startup gate

The MP2762A policy requires evidence that external power is present before it may acquire or renew its 90-second charge-control lease. During boot, the TCPM `online` value can temporarily be `0` even while the charger IC reports input power. Using `online` as the eligibility gate caused the policy to wait through that startup window.

The policy now uses MP2762A register `REG13` bit 1 (`POWER_GOOD`) as the input evidence. TCPM `online` remains in the status record for diagnosis; by itself it cannot authorize inhibition or renewal. The policy retains the temperature, fault, recovery, ownership, hysteresis, readback, explicit `--control`, and kernel lease-support checks. It does not enable charging when another owner or firmware has disabled it.

## Device check

After the change, a real startup with `online=0` and `POWER_GOOD=1` acquired the inhibit automatically at 37.82 seconds and renewed it at 52.84 seconds. Five later samples over the short observation window reported 88% battery, 0 µA, `Not charging`, and 36.7 °C. The policy and its kernel lease path remained active with no reported policy error.

The later persistent r4 boot repeated the startup check: inhibit at 38.11 seconds, renewal at 53.12 seconds, and zero policy errors. These are separate short observations, not a complete charge/resume cycle.

This confirms the startup gate and a short inhibit/renew window only. A full charge/resume cycle, unplug behavior, long-term operation, and other devices were not validated. The userspace script expects the matching opt-in kernel interface and must not be used as a standalone charge controller on an incompatible kernel.

Run the host-side stateful policy checks with:

```sh
python3 tests/Test-ChargePolicy.py
```
