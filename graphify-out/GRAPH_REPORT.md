# Graph Report - /home/mk/Dev_Env/Ascendo  (2026-04-27)

## Corpus Check
- 11 files · ~28,972 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 169 nodes · 473 edges · 9 communities detected
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 85 edges (avg confidence: 0.78)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]

## God Nodes (most connected - your core abstractions)
1. `DevSyncError` - 31 edges
2. `verify_full_state()` - 21 edges
3. `import_overlay()` - 19 edges
4. `main()` - 18 edges
5. `export_candidates()` - 17 edges
6. `safe_relpath()` - 16 edges
7. `load_config()` - 16 edges
8. `Logger` - 15 edges
9. `normalize_relpath()` - 15 edges
10. `classify_repo()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `StaticDevSyncConfigTests` --uses--> `DevSyncError`  [INFERRED]
  /home/mk/Dev_Env/Ascendo/tests/test_dev_sync_safety.py → /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_core.py
- `DevSyncError` --uses--> `FileProviderStatus`  [INFERRED]
  /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_core.py → /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_proton_status.py
- `DevSyncError` --calls--> `manifest_files()`  [INFERRED]
  /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_core.py → /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_proton_status.py
- `DevSyncError` --calls--> `main()`  [INFERRED]
  /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_core.py → /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_proton_status.py
- `DevSyncError` --calls--> `main()`  [INFERRED]
  /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_core.py → /home/mk/Dev_Env/Ascendo/dev-sync/dev_sync_purge_quarantine.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.1
Nodes (20): compare_overlay_content(), config_defaults(), detect_proton_drive_path(), dirty_tracked_entries(), ExportResult, files_match(), FullVerificationResult, git_output() (+12 more)

### Community 1 - "Community 1"
Cohesion: 0.2
Nodes (12): create_provider(), export_candidates(), import_overlay(), LocalFileSystemProvider, Logger, RCloneProvider, rsync_available(), run_command() (+4 more)

### Community 2 - "Community 2"
Cohesion: 0.13
Nodes (20): config_path(), ConfigReviewRequired, create_default_config(), DevSyncConfig, load_config(), print_config_hint(), repo_root_from_script(), build_parser() (+12 more)

### Community 3 - "Community 3"
Cohesion: 0.17
Nodes (24): Classification, classify_repo(), directory_exclude_patterns(), expand_entries_to_files(), git_output_z(), ignored_files(), lexists(), local_working_tree_files() (+16 more)

### Community 4 - "Community 4"
Cohesion: 0.16
Nodes (18): copy_relpaths(), DevSyncError, manifest_path(), read_json(), read_manifest(), resolve_under(), rsync_transfer(), safe_relpath() (+10 more)

### Community 5 - "Community 5"
Cohesion: 0.17
Nodes (17): log_section(), require_local_provider(), build_parser(), evaluate_path(), expected_paths(), FileProviderStatus, main(), manifest_files() (+9 more)

### Community 6 - "Community 6"
Cohesion: 0.22
Nodes (1): CloudProvider

### Community 7 - "Community 7"
Cohesion: 0.43
Nodes (1): StaticDevSyncConfigTests

### Community 8 - "Community 8"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **Thin community `Community 8`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DevSyncError` connect `Community 4` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.210) - this node is a cross-community bridge._
- **Why does `CloudProvider` connect `Community 6` to `Community 0`, `Community 1`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Why does `StaticDevSyncConfigTests` connect `Community 7` to `Community 4`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `DevSyncError` (e.g. with `NullLogger` and `DevSyncPathSafetyTests`) actually correct?**
  _`DevSyncError` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `main()` (e.g. with `repo_root_from_script()` and `dirty_tracked_entries()`) actually correct?**
  _`main()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.1 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.13 - nodes in this community are weakly interconnected._