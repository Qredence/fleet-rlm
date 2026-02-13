# Changelog

## 0.1.0 (2026-02-13)

Full Changelog: [v0.0.1...v0.1.0](https://github.com/Qredence/fleet-rlm/compare/v0.0.1...v0.1.0)

### Features

* add AUTHORS, CONTRIBUTING, and LICENSE files; update README and pyproject.toml ([dd41d99](https://github.com/Qredence/fleet-rlm/commit/dd41d99275ae1c27f440f5a99c205a88c2a2c418))
* add automated release workflow and update releasing instructions ([bb64871](https://github.com/Qredence/fleet-rlm/commit/bb6487115f54d74dbcda1a5f545b27279c34f758))
* add comprehensive documentation including concepts, getting started guide, CLI reference, tutorials, and troubleshooting ([679444e](https://github.com/Qredence/fleet-rlm/commit/679444ee82032c1f18092cb89bb39b860adb3534))
* Add RLM agents for modal interpreter, orchestrator, specialist, and subcall with detailed workflows and diagnostics ([72f1809](https://github.com/Qredence/fleet-rlm/commit/72f180929138b58b135ad1a5f3980783fabed023))
* **ci:** add retry logic for TestPyPI smoke test to handle indexing delay ([09d3d1d](https://github.com/Qredence/fleet-rlm/commit/09d3d1dd2125ff646d5b162bc0f80636b3b38499))
* Implement long-context document processing with chunking strategies ([896c2c1](https://github.com/Qredence/fleet-rlm/commit/896c2c1c1f80b5361faea220f11c9f0987a401c5))
* remove legacy Python TUI and document Claude Code workflows ([eb8421c](https://github.com/Qredence/fleet-rlm/commit/eb8421cddeef1c1b4d73e23215a9571ac7c48382))


### Bug Fixes

* address PR review comments across server, interpreter, and docs ([837f0e4](https://github.com/Qredence/fleet-rlm/commit/837f0e449b69ad5c04a00d2960efaa3d5314b688))
* **ci:** make TestPyPI optional to handle trusted publisher conflicts ([c5460ab](https://github.com/Qredence/fleet-rlm/commit/c5460abf3ba52f14923d5a25f61470dec7547962))
* Type checking errors and import paths ([795318a](https://github.com/Qredence/fleet-rlm/commit/795318a87697d48ab8d7e35968863a00d626abe3))
* Update runners.py import path for tools module ([5990d2b](https://github.com/Qredence/fleet-rlm/commit/5990d2b01ed47711abd439bff5db99d9ae6e44bb))


### Chores

* add PLANS.md to .gitignore ([5579923](https://github.com/Qredence/fleet-rlm/commit/55799230c7fcc21a05a46792fdec973924e9f122))
* bump 0.4.1 and remediate dependabot alerts ([80b4de9](https://github.com/Qredence/fleet-rlm/commit/80b4de989b037c9a59ca7b33bb45c22dc192d4f2))
* clean up code structure and remove unused code blocks ([f9fcf58](https://github.com/Qredence/fleet-rlm/commit/f9fcf587908d08569f0398052e43f43d0e2965cb))
* cleanup repository structure ([5cf4cdd](https://github.com/Qredence/fleet-rlm/commit/5cf4cddb0a2f10889ce6a3584cfa6e03bedfcbfe))
* prepare fleet-rlm for PyPI release ([d54cc64](https://github.com/Qredence/fleet-rlm/commit/d54cc645e308aeb3d9065004c8951acb01493c53))
* remove outdated releasing documentation from RELEASING.md ([dc9cc7d](https://github.com/Qredence/fleet-rlm/commit/dc9cc7d186b891c7fc8f02e4a3078cf25c8b4c83))
* stop tracking ignored agent and tool state directories ([86cd6c3](https://github.com/Qredence/fleet-rlm/commit/86cd6c34d37511a7d93b3ff80ff0a271bee7d008))
* sync repo ([0fcab20](https://github.com/Qredence/fleet-rlm/commit/0fcab200c3409c4e5edced1f5451accf9b63e697))
* update gitignore to include .codex and refine agent state ([ab191b4](https://github.com/Qredence/fleet-rlm/commit/ab191b4cb908920da1e69ed70554a2fd16e7ecb4))
* update SDK settings ([87d8581](https://github.com/Qredence/fleet-rlm/commit/87d8581ed5d113808a8949ef1af3909acb9a6e06))
* update SDK settings ([b47ff7c](https://github.com/Qredence/fleet-rlm/commit/b47ff7c640beea780c9c032efaef465cdf6d165e))


### Documentation

* add changelog for 0.4.0 release ([f0d69e2](https://github.com/Qredence/fleet-rlm/commit/f0d69e24ea728c51f655ffae8901321d00ad6380))
* Add final restructuring summary ([4a498d3](https://github.com/Qredence/fleet-rlm/commit/4a498d3222f032987b5cf24af2dd57aaaf8f7826))
* Add restructuring progress report ([34c6d70](https://github.com/Qredence/fleet-rlm/commit/34c6d706aeda7ecc03d88ec0fc9f64cd9b3a0d36))


### Refactors

* **ci:** reorganize release workflow for improved readability and maintainability ([d672911](https://github.com/Qredence/fleet-rlm/commit/d672911d34013d9695cc7ca8333e487c92a2e078))
* Consolidate documentation by merging CLAUDE.md into AGENTS.md ([1b21159](https://github.com/Qredence/fleet-rlm/commit/1b21159ab594ce49ed9370b3615730321eb626d8))
* Phase 1 - Extract core layer ([bab9ea0](https://github.com/Qredence/fleet-rlm/commit/bab9ea08e8337d1c1a2115f78253629494be00e0))
* Phase 2 - Create chunking subpackage ([7fc5b75](https://github.com/Qredence/fleet-rlm/commit/7fc5b758de2519acc1bf935444a53a2b95c053bf))
* Phase 3 - Reorganize react system ([57aa0b8](https://github.com/Qredence/fleet-rlm/commit/57aa0b808ee91eb7f76af673a5fffcba8b673535))
* Phase 4 - Create stateful subpackage ([7141592](https://github.com/Qredence/fleet-rlm/commit/7141592c70185f5f8c4cecfdcae2ebcd17207ec2))
* Phase 7 - Create utils subpackage ([9c70af4](https://github.com/Qredence/fleet-rlm/commit/9c70af4f2bbdf7e95628b8cd1bd6f42281e60b1a))
* remove AGENTS.md as it is no longer needed ([ef57174](https://github.com/Qredence/fleet-rlm/commit/ef571741a9f2da7cce6de11bffbb1e53b9298c01))
* remove obsolete test files and clean up test suite ([e63d005](https://github.com/Qredence/fleet-rlm/commit/e63d005e0cbef976e5c56384651275ca9f112be3))
* remove unused modules and clean up codebase ([8512120](https://github.com/Qredence/fleet-rlm/commit/8512120334a249d8ded7cc1b1b229373401e5314))
* Update installation instructions in CONTRIBUTING.md and README.md ([ed8c358](https://github.com/Qredence/fleet-rlm/commit/ed8c3580f88b5b39f254ec82c1092e16aa62fa89))
