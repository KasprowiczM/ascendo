#!/usr/bin/env python3
"""
Inject per-tab help keys into i18n.js.
Adds help_summary, help_details_label, and help_li{1-5}_{b,t} keys for all 12 views.
"""

import re

i18n_path = "app/frontend/i18n.js"

# Read the file
with open(i18n_path, encoding="utf-8") as f:
    content = f.read()

# Help texts for each view (EN and PL)
EN_HELP = {
    "overview": {
        "help_summary": "Quick status snapshot — see your system health, latest run, and what needs updating across all sources.",
        "help_details_label": "What does each section do? Click to expand",
        "help_li1_b": "Last run",
        "help_li1_t": "Status of the most recent check/plan/apply/verify run. Shows duration, profile, and any errors or warnings.",
        "help_li2_b": "System health",
        "help_li2_t": "5-component health check: winget, msstore, registry ARP, Windows Update, UAC elevation. Green = OK, red = issue.",
        "help_li3_b": "Git status",
        "help_li3_t": "Current branch, dirty/clean state, and commits ahead/behind origin. Helps you track if you need to push or pull.",
        "help_li4_b": "Quick actions",
        "help_li4_t": "One-click buttons for Quick check, Safe update, Full update, and dry-run demo. Safe = skips driver updates.",
        "help_li5_b": "Post-run health",
        "help_li5_t": "Health re-check after an apply phase to confirm everything is still working. Click Re-check anytime.",
    },
    "apps": {
        "help_summary": "Track which installed apps are managed by Ascendo and which are excluded from automatic updates.",
        "help_details_label": "How does the Apps list work? Click to expand",
        "help_li1_b": "Total / In config / Excluded pills",
        "help_li1_t": "Summary counts at the top. 'In config' = managed by Ascendo; 'Excluded' = skipped on all future runs.",
        "help_li2_b": "Package list table",
        "help_li2_t": "Every app detected on your system. Columns: installed version, candidate upgrade, source (winget/msstore/arp), and in-config checkbox.",
        "help_li3_b": "In config checkbox",
        "help_li3_t": "Checked = this app will be updated on future runs. Unchecked = Ascendo will ignore it (does not uninstall).",
        "help_li4_b": "Add/Remove buttons",
        "help_li4_t": "Toggle membership in the update config. Useful when you discover a new app and want to include it.",
    },
    "categories": {
        "help_summary": "Browse all 4 update sources (winget, Microsoft Store, registry ARP, Windows Update) with a unified 5-phase pipeline.",
        "help_details_label": "How do categories work? Click to expand",
        "help_li1_b": "Category row",
        "help_li1_t": "Each source is a row. Shows: total items, OK count, outdated count, missing count. Click to expand the package list.",
        "help_li2_b": "Phase buttons",
        "help_li2_t": "5 buttons per category: check (read-only), plan (preview), apply (execute), verify (confirm), cleanup (prune). Run them in order.",
        "help_li3_b": "Installed / candidate versions",
        "help_li3_t": "Each package row shows what's installed vs. what's available. Green = current, red = update available, gray = up-to-date.",
        "help_li4_b": "Add widget",
        "help_li4_t": "Add a new package to your config so it gets updated on future runs. Pick a category, type the package name, click +Add.",
        "help_li5_b": "Status pills",
        "help_li5_t": "Color-coded status: OK (green), outdated (yellow), failed (red), skipped (gray). Hover for a tooltip.",
    },
    "run": {
        "help_summary": "Manually start a run with custom options: pick profile, category, phase, and dry-run mode. Watch live progress.",
        "help_details_label": "How does the Run Center work? Click to expand",
        "help_li1_b": "Profile selector",
        "help_li1_t": "Quick = read-only checks. Safe = no drivers. Full = all sources + drivers. Your choice; profile affects what runs.",
        "help_li2_b": "Only selector",
        "help_li2_t": "Limit the run to a single category (e.g., just winget). Leave blank to run all.",
        "help_li3_b": "Phase selector",
        "help_li3_t": "Override the default phases for this run. Normally profile decides; you can skip to e.g., just apply.",
        "help_li4_b": "Dry run checkbox",
        "help_li4_t": "When checked, apply phase emits 'planned' items without actually upgrading. Perfect for seeing what would happen.",
        "help_li5_b": "Live detail panel",
        "help_li5_t": "Shows package-by-package progress as the run executes. Scroll to see which packages are being updated in real time.",
    },
    "history": {
        "help_summary": "Browse all past runs: status, profile, phase counts, duration, and reboot flag. Click a run to inspect sidecars.",
        "help_details_label": "How do I read the History tab? Click to expand",
        "help_li1_b": "Started timestamp",
        "help_li1_t": "When the run started (local time). Sorted newest first so recent runs are at the top.",
        "help_li2_b": "Profile column",
        "help_li2_t": "Which profile was used: quick, safe, or full. Tells you what sources were updated.",
        "help_li3_b": "Status / Duration",
        "help_li3_t": "Overall result and how long it took. Color-coded: green = success, yellow = warnings, red = failure.",
        "help_li4_b": "Run ID",
        "help_li4_t": "Unique identifier for the run. Use this to inspect sidecars in the Logs tab or share with support.",
    },
    "logs": {
        "help_summary": "Inspect the JSON sidecar and plain-text log for any run. Sidecar contains all items, status, errors, messages.",
        "help_details_label": "How do I read Logs? Click to expand",
        "help_li1_b": "Run dropdown",
        "help_li1_t": "Pick which run to inspect from the list. Shows started time + profile so you know which one you want.",
        "help_li2_b": "Phase detail table",
        "help_li2_t": "All phases in the run (check / plan / apply / verify / cleanup). Click a phase to view its plain-text log below.",
        "help_li3_b": "JSON link",
        "help_li3_t": "Click to view the machine-readable sidecar JSON (ascendo/run/v1 schema). Shows items[], messages[], etc.",
        "help_li4_b": "Plain-text log",
        "help_li4_t": "Human-readable log output from the phase. Useful for debugging or understanding what happened.",
    },
    "sync": {
        "help_summary": "Git push/pull, cloud overlay export (dev-sync), and cloud provider setup. Keeps your config in sync.",
        "help_details_label": "What is Sync? Click to expand",
        "help_li1_b": "Git panel",
        "help_li1_t": "Fetch (check for changes), Pull (fast-forward merge), Push (send your commits). Git is always on GitHub.",
        "help_li2_b": "Cloud overlay panel",
        "help_li2_t": "Export your private files (.env.local, agent settings, keys) to a cloud provider. Copy semantics — no deletes.",
        "help_li3_b": "Provider config",
        "help_li3_t": "Pick where to store the overlay: Proton Drive, Google Drive, Dropbox, OneDrive, WebDAV, S3, or local folder.",
        "help_li4_b": "Test connection",
        "help_li4_t": "Verify your rclone config works before the first real export. Catches auth issues early.",
        "help_li5_b": "Output panel",
        "help_li5_t": "Shows the raw output from git, rclone, and dev-sync commands. Useful for debugging.",
    },
    "suggest": {
        "help_summary": "Rule-based + optional AI recommendations for updates and optimizations. Always shown as a diff — nothing applies without your approval.",
        "help_details_label": "How do suggestions work? Click to expand",
        "help_li1_b": "Suggestion list",
        "help_li1_t": "Each suggestion shows a rule name, confidence level, and a brief rationale. All are harmless — just recommendations.",
        "help_li2_b": "Apply button",
        "help_li2_t": "Approve a suggestion and add it to your update plan (does not execute, just queues it for the next run).",
        "help_li3_b": "Dismiss button",
        "help_li3_t": "Reject a suggestion. You won't see it again for 30 days.",
        "help_li4_b": "AI provider config",
        "help_li4_t": "Optional: pick an LLM (Claude, ChatGPT, Gemini, Ollama, etc.) to enrich heuristic suggestions with reasoning.",
    },
    "hosts": {
        "help_summary": "Read-only SSH preflight across remote machines. Check status of Ascendo installations on other boxes.",
        "help_details_label": "How do remote hosts work? Click to expand",
        "help_li1_b": "Add host button",
        "help_li1_t": "Register a new host by SSH alias (must be in ~/.ssh/config with key auth, no passwords).",
        "help_li2_b": "Status columns",
        "help_li2_t": "Hostname, OS, kernel, repo path, last run time, overall status. Gathered via SSH — no mutations.",
        "help_li3_b": "Refresh all button",
        "help_li3_t": "Fetch fresh status from all hosts. Takes a few seconds depending on SSH latency.",
        "help_li4_b": "Edit / Remove",
        "help_li4_t": "Update host details or unregister a host. Changes persist to config/hosts.toml.",
    },
    "settings": {
        "help_summary": "Configure defaults (profile, snapshot, notifications), appearance (theme, language), scheduler, AI provider, backup.",
        "help_details_label": "What can I configure? Click to expand",
        "help_li1_b": "Default profile",
        "help_li1_t": "Which profile runs when you click Quick Actions buttons. Quick = read-only, Safe = no drivers, Full = all.",
        "help_li2_b": "Pre-apply snapshot",
        "help_li2_t": "Automatically create a VSS shadow copy before apply phase. Lets you roll back if something goes wrong.",
        "help_li3_b": "Theme / Language",
        "help_li3_t": "Dark (default), Light, or Auto (match system). Language: English or Polish (settings page only).",
        "help_li4_b": "Scheduler setup",
        "help_li4_t": "Install a Task Scheduler entry to run Ascendo weekly. Respects battery + focus + maintenance window settings.",
        "help_li5_b": "AI provider / Backup",
        "help_li5_t": "Optional LLM for suggestions. Export/import config bundles (lists, exclusions, settings) as tar.gz.",
    },
    "help": {
        "help_summary": "Complete Ascendo guide: installation, CLI cheat-sheet, scripts reference, config files, scheduler, snapshots, troubleshooting.",
        "help_details_label": "How do I navigate Help? Click to expand",
        "help_li1_b": "Table of contents",
        "help_li1_t": "11 sections: Install, First run, CLI, Scripts, Config, Dashboard, Scheduler, Snapshots, Dev-sync, AI, Troubleshooting.",
        "help_li2_b": "Installation instructions",
        "help_li2_t": "Three paths: packaged installer (EXE/MSI), winget command, or git clone for dev. Re-run install-dev.ps1 anytime.",
        "help_li3_b": "Troubleshooting section",
        "help_li3_t": "Common issues (blank tab, already-running, password failures) and fixes. Check here first if something seems broken.",
        "help_li4_b": "Script reference table",
        "help_li4_t": "All managers (winget, msstore, arp, windows_update) + helpers (snapshot, scheduler, elevation) explained.",
    },
    "about": {
        "help_summary": "Version info, host system details, and release notes. See what Ascendo version you're running and check for updates.",
        "help_details_label": "What's in About? Click to expand",
        "help_li1_b": "Application panel",
        "help_li1_t": "Ascendo version, build date, and available update info (if Update Notifier is configured in Settings).",
        "help_li2_b": "Host system panel",
        "help_li2_t": "Your machine hostname, OS version, architecture (x64), and Python version running the dashboard.",
        "help_li3_b": "Release notes section",
        "help_li3_t": "Auto-generated from git history. Shows features, fixes, and changes for each version down to your current build.",
        "help_li4_b": "Check for updates button",
        "help_li4_t": "Look for newer Ascendo versions on GitHub (if GitHub repo is configured). Never auto-installs — read-only.",
    },
}

PL_HELP = {
    "overview": {
        "help_summary": "Szybkie podsumowanie statusu — zobacz kondycję systemu, ostatni run i co wymaga aktualizacji we wszystkich źródłach.",
        "help_details_label": "Do czego służy każda sekcja? Kliknij aby rozwinąć",
        "help_li1_b": "Ostatni run",
        "help_li1_t": "Status ostatniego runu (check/plan/apply/verify). Pokazuje czas trwania, profil i ewentualne błędy.",
        "help_li2_b": "Kondycja systemu",
        "help_li2_t": "5-komponentowy test zdrowia: winget, msstore, registry ARP, Windows Update, UAC. Zielony = OK, czerwony = problem.",
        "help_li3_b": "Status Git",
        "help_li3_t": "Bieżąca gałąź, stan (brudny/czysty), commity ahead/behind origin. Pomaga śledzić czy trzeba push/pull.",
        "help_li4_b": "Szybkie akcje",
        "help_li4_t": "Przyciski jednym klikiem: Quick check, Safe update, Full update, dry-run demo. Safe pomija sterowniki.",
        "help_li5_b": "Kondycja po uruchomieniu",
        "help_li5_t": "Test zdrowia po fazie apply aby potwierdzić że wszystko działa. Kliknij Re-check w dowolnym momencie.",
    },
    "apps": {
        "help_summary": "Śledź które aplikacje zarządza Ascendo i które są wyłączone z automatycznych aktualizacji.",
        "help_details_label": "Jak działa lista aplikacji? Kliknij aby rozwinąć",
        "help_li1_b": "Liczniki: Total / W konfigu / Wyłączone",
        "help_li1_t": "Podsumowanie u góry. 'W konfigu' = zarządzane przez Ascendo; 'Wyłączone' = pomijane na przyszłe runy.",
        "help_li2_b": "Tabela aplikacji",
        "help_li2_t": "Wszystkie aplikacje w systemie. Kolumny: zainstalowana wersja, dostępna wersja, źródło, checkbox w-konfigu.",
        "help_li3_b": "Checkbox w-konfigu",
        "help_li3_t": "Zaznaczone = aplikacja będzie aktualizowana. Niezaznaczone = Ascendo ją ignoruje (nie odinstalowuje).",
        "help_li4_b": "Przyciski Add/Remove",
        "help_li4_t": "Przełącz czy aplikacja jest w konfigu. Przydatne gdy odkryjesz nową aplikację którą chcesz uwzględnić.",
    },
    "categories": {
        "help_summary": "Przeglądaj wszystkie 4 źródła (winget, Microsoft Store, registry ARP, Windows Update) z ujednoliconym pipeline'em 5 faz.",
        "help_details_label": "Jak działają kategorie? Kliknij aby rozwinąć",
        "help_li1_b": "Rząd kategorii",
        "help_li1_t": "Każde źródło to rząd. Pokazuje: razem, OK, do aktualizacji, brakujące. Kliknij aby rozwinąć listę pakietów.",
        "help_li2_b": "Przyciski faz",
        "help_li2_t": "5 przycisków per kategoria: check (read-only), plan (podgląd), apply (wykonanie), verify (potwierdzenie), cleanup.",
        "help_li3_b": "Zainstalowana / dostępna wersja",
        "help_li3_t": "Każdy pakiet pokazuje co zainstalowane vs dostępne. Zielony = bieżący, czerwony = update, szary = aktualny.",
        "help_li4_b": "Widget dodawania",
        "help_li4_t": "Dodaj nowy pakiet do konfigu. Wybierz kategorię, wpisz nazwę, kliknij +Add.",
        "help_li5_b": "Pillsy statusu",
        "help_li5_t": "Kolorowe ikony: OK (zielony), zastarza (żółty), błąd (czerwony), pominięto (szary).",
    },
    "run": {
        "help_summary": "Ręcznie uruchom run z opcjami: wybierz profil, kategorię, fazę, tryb dry-run. Obserwuj postęp na żywo.",
        "help_details_label": "Jak działa Centrum uruchamiania? Kliknij aby rozwinąć",
        "help_li1_b": "Selektor profilu",
        "help_li1_t": "Quick = tylko sprawdzenie. Safe = bez sterowników. Full = wszystko. Profil określa co się uruchomi.",
        "help_li2_b": "Selektor Only",
        "help_li2_t": "Ogranicz run do jednej kategorii (np. tylko winget). Puste = wszystkie.",
        "help_li3_b": "Selektor fazy",
        "help_li3_t": "Nadpisz domyślne fazy dla tego runu. Normalnie profil decyduje; możesz pominąć do np. tylko apply.",
        "help_li4_b": "Checkbox Dry run",
        "help_li4_t": "Zaznaczony = apply emituje 'planned' bez rzeczywistych aktualizacji. Idealny do sprawdzenia co by się stało.",
        "help_li5_b": "Panel live detail",
        "help_li5_t": "Pokazuje postęp pakiet po pakiecie w trakcie wykonywania. Przewiń aby zobaczyć które pakiety się aktualizują.",
    },
    "history": {
        "help_summary": "Przeglądaj wszystkie minionych runy: status, profil, liczby faz, czas, flagę restartu. Kliknij run aby zobaczyć sidecary.",
        "help_details_label": "Jak czytać kartę Historia? Kliknij aby rozwinąć",
        "help_li1_b": "Timestamp startu",
        "help_li1_t": "Kiedy run się zaczął (czas lokalny). Posortowane najnowsze na górze.",
        "help_li2_b": "Kolumna profilu",
        "help_li2_t": "Który profil: quick, safe czy full. Mówi które źródła były aktualizowane.",
        "help_li3_b": "Status / Czas trwania",
        "help_li3_t": "Ogólny wynik i jak długo trwał. Kolorowe: zielony = sukces, żółty = ostrzeżenia, czerwony = błąd.",
        "help_li4_b": "ID runu",
        "help_li4_t": "Unikalny identyfikator. Użyj tego aby inspektować sidecary w karcie Logi lub dzielić z supportem.",
    },
    "logs": {
        "help_summary": "Inspektuj JSON sidecar i plain-text log dla dowolnego runu. Sidecar zawiera wszystkie itemy, status, błędy.",
        "help_details_label": "Jak czytać Logi? Kliknij aby rozwinąć",
        "help_li1_b": "Dropdown runu",
        "help_li1_t": "Wybierz który run inspektować z listy. Pokazuje czas startu + profil.",
        "help_li2_b": "Tabela szczegółów faz",
        "help_li2_t": "Wszystkie fazy w runie (check/plan/apply/verify/cleanup). Kliknij fazę aby zobaczyć jej log poniżej.",
        "help_li3_b": "Link JSON",
        "help_li3_t": "Kliknij aby zobaczyć machine-readable sidecar (ascendo/run/v1 schema). Pokazuje items[], messages[] itd.",
        "help_li4_b": "Plain-text log",
        "help_li4_t": "Czytany dla człowieka output z fazy. Przydatny do debuggowania lub zrozumienia co się stało.",
    },
    "sync": {
        "help_summary": "Git push/pull, export cloud overlay (dev-sync), setup providera chmury. Synchronizuj konfigu.",
        "help_details_label": "Czym jest Sync? Kliknij aby rozwinąć",
        "help_li1_b": "Panel Git",
        "help_li1_t": "Fetch (sprawdź zmiany), Pull (fast-forward merge), Push (wyślij commity). Git zawsze na GitHub.",
        "help_li2_b": "Panel cloud overlay",
        "help_li2_t": "Eksportuj prywatne pliki (.env.local, ustawienia, klucze) do providera. Copy semantyka — bez deletes.",
        "help_li3_b": "Konfiguracja providera",
        "help_li3_t": "Gdzie przechowywać: Proton Drive, Google Drive, Dropbox, OneDrive, WebDAV, S3, czy folder lokalny.",
        "help_li4_b": "Test połączenia",
        "help_li4_t": "Zweryfikuj że rclone config działa przed pierwszym exportem. Łapie problemy z autentykacją wcześnie.",
        "help_li5_b": "Panel output",
        "help_li5_t": "Pokazuje raw output z git, rclone, dev-sync poleceń. Przydatny do debuggowania.",
    },
    "suggest": {
        "help_summary": "Rekomendacje oparte na regułach + opcjonalnie AI dla aktualizacji i optymalizacji. Zawsze jako diff — nic bez Twojej zgody.",
        "help_details_label": "Jak działają sugestie? Kliknij aby rozwinąć",
        "help_li1_b": "Lista sugestii",
        "help_li1_t": "Każda sugestia pokazuje nazwę reguły, poziom pewności, i uzasadnienie. Wszystkie są nieszkodliwe.",
        "help_li2_b": "Przycisk Apply",
        "help_li2_t": "Zatwierdź sugestię i dodaj do planu (nie wykonuje, tylko kolejkuje na następny run).",
        "help_li3_b": "Przycisk Dismiss",
        "help_li3_t": "Odrzuć sugestię. Nie zobaczysz jej przez 30 dni.",
        "help_li4_b": "Konfiguracja AI providera",
        "help_li4_t": "Opcjonalne: wybierz LLM (Claude, ChatGPT, Gemini, Ollama) aby wzbogacić sugestie z uzasadnieniem.",
    },
    "hosts": {
        "help_summary": "Read-only SSH preflight na zdalne maszyny. Sprawdź status instalacji Ascendo na innych boxach.",
        "help_details_label": "Jak działają zdalne hosty? Kliknij aby rozwinąć",
        "help_li1_b": "Przycisk Add host",
        "help_li1_t": "Zarejestruj nowego hosta przez SSH alias (musi być w ~/.ssh/config z key auth, bez haseł).",
        "help_li2_b": "Kolumny statusu",
        "help_li2_t": "Hostname, OS, kernel, ścieżka repo, ostatni run, status ogólny. Pobrane via SSH — bez mutacji.",
        "help_li3_b": "Przycisk Refresh all",
        "help_li3_t": "Pobierz świeży status ze wszystkich hostów. Zajmuje kilka sekund w zależności od SSH latency.",
        "help_li4_b": "Edit / Remove",
        "help_li4_t": "Aktualizuj szczegóły hosta lub usuń rejestrację. Zmiany trwają w config/hosts.toml.",
    },
    "settings": {
        "help_summary": "Konfiguruj domyślne (profil, snapshot, notyfikacje), wygląd (temat, język), scheduler, AI, backup.",
        "help_details_label": "Co mogę konfigurować? Kliknij aby rozwinąć",
        "help_li1_b": "Profil domyślny",
        "help_li1_t": "Który profil działa gdy klikniesz Quick Actions. Quick = read-only, Safe = bez drivers, Full = wszystko.",
        "help_li2_b": "Snapshot przed apply",
        "help_li2_t": "Auto snapshot VSS przed fazą apply. Pozwala na rollback jeśli coś pójdzie nie tak.",
        "help_li3_b": "Temat / Język",
        "help_li3_t": "Dark (domyślny), Light, Auto (pasuj do systemu). Język: English czy Polski.",
        "help_li4_b": "Setup schedulera",
        "help_li4_t": "Zainstaluj Task Scheduler entry do cotygodniowego runu. Respektuje baterię + focus + maintenance window.",
        "help_li5_b": "AI provider / Backup",
        "help_li5_t": "Opcjonalne LLM do sugestii. Export/import bundlów konfigu (listy, exclusions, ustawienia) jako tar.gz.",
    },
    "help": {
        "help_summary": "Kompletny przewodnik Ascendo: instalacja, CLI cheat-sheet, referencja skryptów, pliki konfigu, scheduler, snapshots, troubleshooting.",
        "help_details_label": "Jak nawigować Help? Kliknij aby rozwinąć",
        "help_li1_b": "Spis treści",
        "help_li1_t": "11 sekcji: Install, First run, CLI, Scripts, Config, Dashboard, Scheduler, Snapshots, Dev-sync, AI, Troubleshooting.",
        "help_li2_b": "Instrukcje instalacji",
        "help_li2_t": "Trzy ścieżki: packaged installer (EXE/MSI), polecenie winget, lub git clone dla dev. Uruchamiaj install-dev.ps1 zawsze.",
        "help_li3_b": "Sekcja Troubleshooting",
        "help_li3_t": "Częste problemy (pusta karta, już uruchamiany, błędy hasła) i rozwiązania. Sprawdź tu najpierw.",
        "help_li4_b": "Tabela referencji skryptów",
        "help_li4_t": "Wszystkie managery (winget, msstore, arp, windows_update) + helpery (snapshot, scheduler, elevation) wyjaśnione.",
    },
    "about": {
        "help_summary": "Informacje o wersji, szczegóły systemu hosta, i release notes. Zobacz wersję Ascendo i sprawdź aktualizacje.",
        "help_details_label": "Co jest w About? Kliknij aby rozwinąć",
        "help_li1_b": "Panel aplikacji",
        "help_li1_t": "Wersja Ascendo, data build, i info o dostępnych aktualizacjach (jeśli Update Notifier skonfigurowany).",
        "help_li2_b": "Panel systemu hosta",
        "help_li2_t": "Hostname, wersja OS, architektura (x64), i wersja Pythona uruchamiającego dashboard.",
        "help_li3_b": "Sekcja release notes",
        "help_li3_t": "Auto-generated z git history. Pokazuje features, fixes, zmiany dla każdej wersji do Twojej obecnej.",
        "help_li4_b": "Przycisk Check for updates",
        "help_li4_t": "Szukaj nowszych wersji na GitHub (jeśli repo skonfigurowane). Nigdy nie instaluje auto — read-only.",
    },
}

# Function to add help keys to a namespace
def add_help_keys_to_namespace(content, view_id, en_help, pl_help, is_en=True):
    """Insert help keys into the namespace for a given view."""
    help_dict = en_help if is_en else pl_help

    if view_id not in help_dict:
        return content

    help_text = help_dict[view_id]

    # Build the help entry string
    help_entry = ""
    for key, value in help_text.items():
        # Escape quotes properly for JavaScript string
        escaped_value = value.replace('"', '\\"').replace("\n", "\\n")
        help_entry += f'      {key}: "{escaped_value}",\n'

    # Find the view's namespace block (e.g., "overview: {")
    # and insert the help keys before the closing brace
    view_pattern = rf"({view_id}:\s*\{{[^}}]*)"

    # Check if this view already has help keys (skip if yes)
    if f"{view_id}.help_summary" in content:
        return content

    # Find where to insert: look for the last key in the view's namespace
    # We'll search for the view namespace and insert before its closing brace
    # This is tricky with nested structures, so we'll use a simpler approach:
    # Find " {view_id}: {" and then find the first "}" that closes it

    start_pattern = rf"(\s*{view_id}:\s*{{\s*)"

    # Find all occurrences of this pattern
    matches = list(re.finditer(start_pattern, content))
    if not matches:
        print(f"Could not find namespace for {view_id}")
        return content

    # Use the last match (to avoid conflicts with other fields)
    match = matches[-1]
    start_idx = match.end()

    # Now find the closing brace for this namespace
    # We need to match braces properly
    brace_count = 1
    idx = start_idx
    while idx < len(content) and brace_count > 0:
        if content[idx] == '{':
            brace_count += 1
        elif content[idx] == '}':
            brace_count -= 1
        idx += 1

    # Insert before the final closing brace
    insert_pos = idx - 1

    # Make sure we're before a comma or closing brace
    # Back up to just before the closing brace
    while insert_pos > 0 and content[insert_pos - 1] in ' \n':
        insert_pos -= 1

    # Check if there's a trailing comma before the brace
    check_idx = insert_pos - 1
    while check_idx > 0 and content[check_idx] in ' \n':
        check_idx -= 1

    has_trailing_comma = content[check_idx] == ','

    if not has_trailing_comma and check_idx > 0 and content[check_idx] != '{':
        help_entry = ',\n' + help_entry

    content = content[:insert_pos] + help_entry + content[insert_pos:]

    return content

# Add help keys for all views
views = ["overview", "apps", "categories", "run", "history", "logs", "sync", "suggest", "hosts", "settings", "help", "about"]

# Process EN block
for view_id in views:
    content = add_help_keys_to_namespace(content, view_id, EN_HELP, PL_HELP, is_en=True)

# Process PL block
for view_id in views:
    content = add_help_keys_to_namespace(content, view_id, EN_HELP, PL_HELP, is_en=False)

# Write back
with open(i18n_path, 'w', encoding="utf-8") as f:
    f.write(content)

print(f"Successfully added help keys for {len(views)} views to {i18n_path}")
