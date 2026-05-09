# Homebrew formula for Ascendo (cross-platform unified updates orchestrator).
#
# Tap: KasprowiczM/homebrew-tap
# Repo path: Formula/ascendo.rb
#
# Install (once tap is published):
#   brew install KasprowiczM/tap/ascendo
#
# Update mechanism:
#   brew upgrade KasprowiczM/tap/ascendo
#
# This formula installs Ascendo from the GitHub release tarball + sets up
# a per-machine venv at $HOMEBREW_PREFIX/var/ascendo/. The shipped DMG
# remains the recommended path for end users; this formula targets
# CLI-first / power-user installs.
#
# Auto-bumped by .github/workflows/release.yml using
# `dawidd6/action-homebrew-bump-formula`. The url + sha256 must match a
# published GitHub Release tarball.

class Ascendo < Formula
  desc "Cross-platform unified updates orchestrator (CLI + dashboard)"
  homepage "https://github.com/KasprowiczM/ascendo"
  url "https://github.com/KasprowiczM/ascendo/archive/refs/tags/v0.0.7.tar.gz"
  sha256 "<FILL_AT_RELEASE>"
  license "MIT"
  head "https://github.com/KasprowiczM/ascendo.git", branch: "main"

  depends_on "python@3.12"
  depends_on "git"
  depends_on "jq"

  # macOS adapter optional deps. None of these block install — the brew
  # adapter probes for them at runtime and falls back to disabled
  # categories. Listed via `recommends` so power users can install them
  # ahead of time.
  uses_from_macos "curl"

  def install
    # libexec keeps the Python install isolated from any other formula's
    # site-packages / CLI binaries. Standard Homebrew Python pattern.
    libexec.install Dir["*"]

    venv_dir = libexec/"venv"
    system Formula["python@3.12"].opt_bin/"python3.12", "-m", "venv", venv_dir
    venv_python = venv_dir/"bin/python"

    system venv_python, "-m", "pip", "install", "--upgrade", "pip"
    system venv_python, "-m", "pip", "install", "-e", libexec/"core"
    if File.directory?(libexec/"adapters/macos")
      system venv_python, "-m", "pip", "install", "-e", libexec/"adapters/macos"
    end

    # Single CLI entrypoint. brew links it to $HOMEBREW_PREFIX/bin/ascendo.
    (bin/"ascendo").write <<~EOS
      #!/bin/sh
      exec "#{venv_dir}/bin/ascendo" "$@"
    EOS
    chmod 0755, bin/"ascendo"

    # Helper user-scripts get the same shim treatment so things like
    # `ascendo_doctor` and `ascendo_update` show up on PATH automatically.
    Dir[libexec/"bin/user-scripts/*"].each do |src|
      base = File.basename(src)
      next if base == "README.md"
      next if base.end_with?(".ps1")     # PowerShell mirrors irrelevant on macOS
      shim = bin/base
      shim.write <<~EOS
        #!/bin/sh
        export ASCENDO_HOME="${ASCENDO_HOME:-#{libexec}}"
        exec "#{libexec}/bin/user-scripts/#{base}" "$@"
      EOS
      chmod 0755, shim
    end
  end

  def caveats
    <<~EOS
      Ascendo installed via Homebrew. Run:

          ascendo doctor             # 5-component health check
          ascendo                    # default action: full update
          ascendo dashboard          # start dashboard ad-hoc
                                     # then open http://127.0.0.1:8765

      Per-user state lives at ~/.ascendo (run history, sidecars).
      To switch to the developer edition:
          ascendo_update --edition dev

      Prefer the .dmg installer if you want the desktop app shell:
          https://github.com/KasprowiczM/ascendo/releases
    EOS
  end

  test do
    # Smoke test: CLI version + doctor. doctor exits non-zero when no
    # adapter loads (e.g. Linux box with no /Applications), which is OK
    # for the test — we just confirm the binary is wired and reachable.
    output = shell_output("#{bin}/ascendo version")
    assert_match "ascendo", output

    system "#{bin}/ascendo", "--help"
  end
end
