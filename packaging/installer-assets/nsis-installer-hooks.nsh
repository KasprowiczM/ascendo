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
    ; Opt-in service registration via $ASCENDO_INSTALL_AS_SERVICE env var.
    ; A future release will replace this with a Modern UI 2 components page
    ; checkbox; for now, env-var keeps the silent-install path scriptable.
    ReadEnvStr $0 "ASCENDO_INSTALL_AS_SERVICE"
    ${If} $0 == "1"
        DetailPrint "Ascendo: ASCENDO_INSTALL_AS_SERVICE=1 → registering AscendoDashboard service"
        nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\bin\install-service.ps1" -Action install -InstallPath "$INSTDIR"'
        Pop $1
        ${If} $1 != 0
            MessageBox MB_ICONEXCLAMATION "AscendoDashboard service install returned exit $1.$\nYou can re-run install-service.ps1 manually from $INSTDIR\bin\, or use Settings → Service in the dashboard."
        ${EndIf}
    ${Else}
        DetailPrint "Ascendo: post-install OK. Run from Start menu, or install as service via Settings → Service."
    ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
    ; Always tear down the service if present. install-service.ps1 -Action
    ; uninstall returns 0 even when the service doesn't exist (idempotent),
    ; so we can call it unconditionally without a presence check.
    ${If} ${FileExists} "$INSTDIR\bin\install-service.ps1"
        DetailPrint "Ascendo: pre-uninstall → removing AscendoDashboard service if present"
        nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\bin\install-service.ps1" -Action uninstall'
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
    ; Optional cleanup of user data lives here. The default uninstall already
    ; removes the install dir; we do NOT touch %LocalAppData%\Ascendo by
    ; default — that holds the user's run history + settings, which they
    ; might want to keep across re-installs. A future release will add a
    ; "Remove user data" checkbox on the uninstall page.
!macroend
