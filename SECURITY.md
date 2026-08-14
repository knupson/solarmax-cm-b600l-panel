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

The same reasoning is why **WinRing0 is not used**. It is the ring0 driver
LibreHardwareMonitor loads for MSR access; Windows blocks it as a known-vulnerable driver, and
this project does not work around that block. The cost is real — package power and CPU fan RPM
cannot be read at all — and it is accepted rather than bypassed.

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
