# UFS2Tool

FreeBSD UFS1/UFS2 filesystem manager for Windows (.NET 8), used for the
`.ffpkg` (UFS) image format. These assemblies were shipped base64-embedded
inside exFAT Image Builder v4.0.2 and are reused here unmodified.

* `UFS2Tool.exe`  — launcher (manifest requires elevation; only the Dokan
  *mount* feature needs it)
* `UFS2Tool.dll`  — main assembly, targets .NETCoreApp 8.0
* `DokanNet.dll`  — Dokan bindings (mount feature only)
* `UFS2Tool.runtimeconfig.json` — reconstructed; the original was generated
  at extraction time by the host app

Invoked as `dotnet UFS2Tool.dll <command>` so the elevation manifest on the
launcher is bypassed — `makefs` / `newfs -D` / `extract` / `info` / `ls`
all work unelevated. Requires the .NET 8 runtime on the host.
