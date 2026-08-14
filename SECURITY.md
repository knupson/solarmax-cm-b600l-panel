# Security policy

## Reporting a vulnerability

Report privately through GitHub: **Security → Advisories → Report a vulnerability** on this
repository. That channel is private between you and the maintainer.

Please do not open a public issue for anything that could be exploited before there is a fix.

Expect a first reply within about a week. This is a one-person project, not a vendor with a
duty roster; if a fix takes longer than that, you will be told what is happening.

## Why this project needs a security policy at all

This is not an ordinary desktop app. Three things about it are worth knowing before you send
a patch or run it.

### 1. The sensor interface can write to hardware. This project only reads.

Readings come from Gigabyte's **GSA1 ACPI-WMI** interface (`root\WMI`, `GSA1_ACPIMethod`).
Besides the read methods, that interface exposes `PIOWrite*`, `MEMWrite*` and `PCIWrite*` —
**arbitrary writes to I/O ports, to physical memory and to PCI configuration space**. A wrong
address there can corrupt firmware state or brick a board, and it is reachable from any code
that can talk to WMI on an elevated session.

**The driver calls read methods only, and that is a hard rule, not a default.** A pull request
that introduces a write to GSA1 will not be merged without a precise account of which register
it targets and why. If you are unsure whether a method writes, treat it as if it does.

### 1b. The optional sensor DLL can load a vulnerable kernel driver

**This section previously claimed the opposite. It was wrong, and the correction matters more
than the original claim did.**

LibreHardwareMonitor reads CPU package power and per-core figures through **MSRs**, and to
reach those it loads a kernel driver. In builds up to and including **0.9.3** that driver is
**WinRing0**, which is on Microsoft's vulnerable-driver blocklist: it exposes arbitrary kernel
memory read/write to any local process that can open its device, which is the standard
bring-your-own-vulnerable-driver privilege-escalation primitive. Its signing certificate
expired in 2008.

Two details make this easy to miss, and they are why this project got it wrong for weeks:

- **The service is named after the host process, not after the driver.** Loaded from
  `powershell.exe` it registers as `R0powershell` and writes itself as `powershell.sys` beside
  the host executable. Looking for a service called `WinRing0` finds nothing and reports a
  false all-clear.
- **Windows blocking a load attempt is not the same as the driver never loading.** Defender can
  report blocked attempts while an earlier load is still resident.

To check a machine:

```powershell
Get-CimInstance Win32_SystemDriver | Where-Object { $_.Name -match '^R0' } |
    Select-Object Name, State, PathName
```

**Use a LibreHardwareMonitor build that uses PawnIO instead.** PawnIO is a signed, sandboxed
driver whose modules are verified, and it replaces WinRing0 for MSR access. `python -m
vmaxpanel --diagnose` inspects the DLL you supplied and says which of the two it uses.

Nothing here is redistributed by this repository, so which build is installed is the user's
choice -- which is exactly why the diagnostic reports it rather than assuming.

### 2. It runs elevated

The autostart task is registered with `RunLevel HighestAvailable`, because without elevation
there is no GSA1 and no SMART. Anything the engine executes, it executes with those rights.
Keep that in mind for any change that touches process spawning, file paths taken from a
profile, or the sidecar.

### 3. Profiles and bundles are untrusted input

A `.vmaxpanel` bundle is a zip that somebody else built. Import already guards against the two
classic attacks, and both are covered by tests in `tests/test_bundle.py`:

- **Path traversal (zip-slip)**: members resolving outside the destination are refused,
  including absolute paths.
- **Decompression bombs**: per-member and total expanded size are bounded before extraction.

Asset paths inside a profile go through `safe_asset_path`, which refuses anything outside
`vmaxpanel/assets/`. That check exists precisely because the engine runs elevated.

If you find a way past any of these, that is exactly the kind of thing this policy is for.

## In scope

- Escaping the asset directory, or writing outside it, from a profile or a bundle
- Getting code to run through a profile, a bundle, or the editor
- Any path that reaches a GSA1 write method
- Privilege issues in the scheduled task or the installer
- Denial of service against the host from a crafted profile or bundle

## Out of scope

- Physical access to an unlocked machine
- Anything requiring the attacker to already be an administrator, since the engine already
  runs elevated by design
- The optional third-party sensor DLLs (LibreHardwareMonitor) — report those upstream. This
  repository does not redistribute them
- `ffmpeg`, used as an external process for video backgrounds — report upstream
- The vendor's *LCD Control* software and its firmware

## Supported versions

The latest release and the `main` branch. There are no backports.
