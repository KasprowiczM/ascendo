# softwareupdate -l fixtures

Real `softwareupdate -l` output captured from macOS 14.x.

## Format-drift risk

Apple does not document the `softwareupdate -l` text format as stable. Major
macOS releases historically tweaked spacing, key names, and ordering.
Re-capture fixtures and update the parser when:

- Tests start failing on a fresh CI Mac after a macOS upgrade.
- The script's check phase emits items missing a `Title` or `Version` field.
- New `Action: <value>` entries appear (currently we recognize only `restart`).

## Capture command

```bash
softwareupdate -l > /tmp/sample.txt 2>&1
```

Then trim the leading `Software Update Tool` banner if your captured shell
emits extra noise. Whitespace before `Title:` lines is a literal TAB.
