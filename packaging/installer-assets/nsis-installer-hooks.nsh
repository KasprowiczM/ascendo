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
;  Sub-project 4 (Windows service) hook:
;  ─────────────────────────────────────
;  When sub-project 4 lands, it should:
;
;    1. Add a Modern UI 2 components page entry like:
;
;         !insertmacro MUI_PAGE_COMPONENTS
;         Section /o "Run Ascendo as a Windows service (recommended)" SecService
;           ; SectionIn flag ensures the section is OFF by default.
;           StrCpy $InstallAsService "1"
;         SectionEnd
;
;    2. Inside !macro NSIS_HOOK_POSTINSTALL below, gate the existing
;       "do nothing" comment on $InstallAsService:
;
;         ${If} $InstallAsService == "1"
;             nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass \
;               -File "$INSTDIR\\bin\\install-service.ps1" -InstallPath "$INSTDIR"'
;             Pop $0   ; exit code
;             ${If} $0 != 0
;                 MessageBox MB_ICONEXCLAMATION "Service install returned $0; \
;                   you can re-run install-service.ps1 manually from $INSTDIR\\bin."
;             ${EndIf}
;         ${EndIf}
;
;    3. In !macro NSIS_HOOK_PREUNINSTALL, mirror that with uninstall-service.ps1.
;
;  We do NOT implement service registration here yet — sub-project 4 owns it.
; ============================================================================

!macro NSIS_HOOK_PREINSTALL
    ; Reserved for sub-project 4 — pre-install service-related work.
!macroend

!macro NSIS_HOOK_POSTINSTALL
    ; Currently a noop. Sub-project 4 will replace this with the conditional
    ; install-service.ps1 invocation gated on the user's checkbox.
    DetailPrint "Ascendo: post-install hook (no service registration in 0.0.7)"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
    ; Currently a noop. Sub-project 4 will tear down the Windows service here
    ; if it was previously registered (idempotent: try-catch + ignore "service
    ; not found").
    DetailPrint "Ascendo: pre-uninstall hook (no service teardown in 0.0.7)"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
    ; Optional cleanup of user data lives here. The default uninstall already
    ; removes the install dir; we do NOT touch %LocalAppData%\Ascendo unless
    ; the user opts in (tracked separately as a future enhancement — wire a
    ; second checkbox on the uninstall page).
!macroend
