; ============================================================================
;  Ascendo NSIS installer hooks
; ============================================================================
;
;  Tauri 2.x ``bundle.windows.nsis.installerHooks`` injects this file into the
;  generated installer.nsi via ``!include``. The macros below are called by
;  Tauri's NSIS template at well-known points:
;
;    * NSIS_HOOK_PREINSTALL        — before files are written to disk
;    * NSIS_HOOK_POSTINSTALL       — after files are written, before completion
;    * NSIS_HOOK_PREUNINSTALL      — at the start of uninstall
;    * NSIS_HOOK_POSTUNINSTALL     — after the install dir is removed
;
;  A macro that does not exist is silently ignored, so we only define the
;  ones we actually use right now.
;
;  Service registration (sub-project 4 of v0.0.7):
;  ─────────────────────────────────────────────────
;  bin\install-service.ps1 is the canonical front-end for the
;  AscendoDashboard Windows service (NSSM-wrapped FastAPI sidecar).
;
;  Default install path: NO automatic service registration. We don't want
;  to register a system service silently — it has security and start-time
;  implications the user should opt into. The SPA's Settings → Service
;  panel exposes Install / Uninstall buttons that call the same script.
;
;  Power users / silent installs can opt in by setting the environment
;  variable ASCENDO_INSTALL_AS_SERVICE=1 before running the .exe / .msi:
;
;    setx ASCENDO_INSTALL_AS_SERVICE 1   ; one-time
;    Ascendo-0.0.7-x64-setup.exe         ; will register the service
;
;  Uninstall ALWAYS attempts to tear down the service, idempotently —
;  install-service.ps1 -Action uninstall returns 0 if the service is
;  already gone, so it's safe to call unconditionally.
; ============================================================================

!macro NSIS_HOOK_PREINSTALL
    ; (Reserved for future use — currently a noop.)
!macroend

!macro NSIS_HOOK_POSTINSTALL
    ; ── Smart first-run bootstrap ────────────────────────────────────
    ; We invoke first-run-bootstrap-windows.ps1 in non-interactive
    ; mode so the user gets Python ≥ 3.11, git, curl + a verified
    ; `ascendo doctor` without any further clicks. The bootstrap is
    ; idempotent; subsequent re-runs (e.g. after a repair install)
    ; quietly exit.
    ;
    ; Path resolution: bin/build-installer.ps1 stages bin/* into
    ; ui/desktop-tauri/src-tauri/bin-staging/, and tauri.conf.json
    ; bundle.resources mirrors that into $INSTDIR\resources\bin-staging\
    ; on Windows. We probe both the canonical $INSTDIR\bin location
    ; AND the resources mirror so the install works whichever path the
    ; running Tauri version uses.
    ;
    ; Edition + Profile resolution priority:
    ;   1. $ASCENDO_EDITION / $ASCENDO_PROFILE env vars (silent installs)
    ;   2. defaults: Edition=basic, Profile=full
    ;
    ; Failure here is non-fatal to the installer: the user can re-run
    ; the bootstrap manually via `pwsh first-run-bootstrap-windows.ps1`
    ; from $INSTDIR.
    StrCpy $3 ""
    ${If} ${FileExists} "$INSTDIR\bin\first-run-bootstrap-windows.ps1"
        StrCpy $3 "$INSTDIR\bin\first-run-bootstrap-windows.ps1"
    ${ElseIf} ${FileExists} "$INSTDIR\resources\bin-staging\first-run-bootstrap-windows.ps1"
        StrCpy $3 "$INSTDIR\resources\bin-staging\first-run-bootstrap-windows.ps1"
    ${EndIf}
    ${If} $3 != ""
        DetailPrint "Ascendo: running smart first-run bootstrap ($3)"
        nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$3" -NonInteractive'
        Pop $2
        ${If} $2 != 0
            DetailPrint "Ascendo: first-run bootstrap exited $2 — see %LOCALAPPDATA%\Ascendo\first-run.log"
        ${Else}
            DetailPrint "Ascendo: bootstrap complete."
        ${EndIf}
    ${Else}
        DetailPrint "Ascendo: first-run bootstrap script not bundled — skipping."
    ${EndIf}

    ; ── Optional service registration ───────────────────────────────
    ; Opt-in via $ASCENDO_INSTALL_AS_SERVICE env var. A future release
    ; replaces this with a Modern UI 2 components page checkbox; for
    ; now, env-var keeps silent installs scriptable.
    ReadEnvStr $0 "ASCENDO_INSTALL_AS_SERVICE"
    ${If} $0 == "1"
        StrCpy $4 ""
        ${If} ${FileExists} "$INSTDIR\bin\install-service.ps1"
            StrCpy $4 "$INSTDIR\bin\install-service.ps1"
        ${ElseIf} ${FileExists} "$INSTDIR\resources\bin-staging\install-service.ps1"
            StrCpy $4 "$INSTDIR\resources\bin-staging\install-service.ps1"
        ${EndIf}
        ${If} $4 != ""
            DetailPrint "Ascendo: ASCENDO_INSTALL_AS_SERVICE=1 → registering AscendoDashboard service"
            nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$4" -Action install -InstallPath "$INSTDIR"'
            Pop $1
            ${If} $1 != 0
                MessageBox MB_ICONEXCLAMATION "AscendoDashboard service install returned exit $1.$\nYou can re-run install-service.ps1 manually, or use Settings → Service in the dashboard."
            ${EndIf}
        ${Else}
            DetailPrint "Ascendo: install-service.ps1 not bundled — skipping service registration."
        ${EndIf}
    ${Else}
        DetailPrint "Ascendo: post-install OK. Run from Start menu, or install as service via Settings → Service."
    ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
    ; Always tear down the service if present. install-service.ps1 -Action
    ; uninstall returns 0 even when the service doesn't exist (idempotent),
    ; so we can call it unconditionally without a presence check.
    StrCpy $5 ""
    ${If} ${FileExists} "$INSTDIR\bin\install-service.ps1"
        StrCpy $5 "$INSTDIR\bin\install-service.ps1"
    ${ElseIf} ${FileExists} "$INSTDIR\resources\bin-staging\install-service.ps1"
        StrCpy $5 "$INSTDIR\resources\bin-staging\install-service.ps1"
    ${EndIf}
    ${If} $5 != ""
        DetailPrint "Ascendo: pre-uninstall → removing AscendoDashboard service if present"
        nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$5" -Action uninstall'
        Pop $0
        ; Exit 0 = removed or already absent. Anything else is informational
        ; only — we don't block uninstall on service-removal failure since
        ; the user explicitly asked to uninstall.
        ${If} $0 != 0
            DetailPrint "Ascendo: service removal returned $0 (continuing — service may have been removed by hand)"
        ${EndIf}
    ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
    ; Offer to remove per-user state (run history, sidecars, settings).
    ; By default we PRESERVE %LocalAppData%\Ascendo so a re-install picks
    ; up the user's run history. The user opts into a full purge by
    ; setting $ASCENDO_PURGE_USER_DATA=1 (silent uninstalls) OR clicking
    ; Yes on the dialog (interactive uninstalls).
    ReadEnvStr $0 "ASCENDO_PURGE_USER_DATA"
    StrCmp $0 "1" purge_yes 0
    IfSilent skip_prompt 0
    MessageBox MB_YESNO|MB_ICONQUESTION \
        "Also remove Ascendo user data?$\r$\n$\r$\nThis will delete:$\r$\n  - %LOCALAPPDATA%\Ascendo (run history, logs, marker files)$\r$\n$\r$\nClick No to keep it (recommended if you'll reinstall later)." \
        IDYES purge_yes IDNO skip_prompt
    purge_yes:
        DetailPrint "Ascendo: purging %LOCALAPPDATA%\Ascendo"
        RMDir /r "$LOCALAPPDATA\Ascendo"
    skip_prompt:
!macroend
