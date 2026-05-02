# Ascendo — winget submission manifest

Three YAML files per the [winget package manifest spec](https://github.com/microsoft/winget-pkgs/blob/master/doc/manifest/schema/1.6.0/version.md):

| File | Purpose |
|---|---|
| [`Ascendo.Ascendo.yaml`](Ascendo.Ascendo.yaml) | Version manifest — root pointer |
| [`Ascendo.Ascendo.installer.yaml`](Ascendo.Ascendo.installer.yaml) | Installer manifest — URLs, hashes, switches |
| [`Ascendo.Ascendo.locale.en-US.yaml`](Ascendo.Ascendo.locale.en-US.yaml) | en-US package metadata |

## Submitting a release

1. **Build the installers.** Run `bin/build-installer.ps1` from the repo
   root (Windows). It produces `dist/Ascendo-<ver>-x64.msi` and
   `dist/Ascendo-<ver>-x64-setup.exe` and prints SHA-256 hashes for both.
2. **Bump the version.** Update `PackageVersion: 0.0.7` in all three
   files to the new release version (must match the GitHub release tag,
   sans `v` prefix).
3. **Fill the placeholders** in `Ascendo.Ascendo.installer.yaml`:
   - `<RELEASE_URL_NSIS_EXE>` → URL of the `-setup.exe` asset on the
     GitHub Release page.
   - `<RELEASE_URL_MSI>` → URL of the `.msi` asset on the GitHub Release
     page.
   - `<FILL_AT_RELEASE>` (twice) → SHA-256 hashes from the build script
     summary.
   - `ReleaseDate: 2026-05-02` → today's release date (YYYY-MM-DD).
4. **Validate locally** (one-time, requires winget 1.6+):
   ```powershell
   winget validate --manifest packaging\winget-manifest\
   ```
5. **Test install locally** (requires `--manifest` permissions):
   ```powershell
   winget install --manifest packaging\winget-manifest\
   ```
6. **Submit upstream.** Open a PR against
   [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs)
   placing the three files at
   `manifests/a/Ascendo/Ascendo/0.0.7/`. The `wingetcreate` tool
   automates this:
   ```powershell
   wingetcreate submit --token <ghpat> packaging\winget-manifest\
   ```

## After publication

Once Microsoft merges the PR (typically <48h), end users get:

```powershell
winget install --id Ascendo.Ascendo
winget upgrade Ascendo.Ascendo            # next release
winget uninstall Ascendo.Ascendo
```

## Polish locale

Add `Ascendo.Ascendo.locale.pl-PL.yaml` modelled on `.locale.en-US.yaml`
once we have a translated description, and bump
`ManifestVersion: 1.6.0` consistently. winget surfaces locale-specific
descriptions to users with matching system language preferences.
