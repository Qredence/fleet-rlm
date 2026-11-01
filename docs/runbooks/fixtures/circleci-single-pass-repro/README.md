# CircleCI single-pass coverage parser reproduction

This standalone fixture checks whether CircleCI's testsuite extension accepts
the pinned `pytest-circleci-coverage` output and maps a test-file atom to a source
file. It is retained as a compatibility record; Fleet currently uses per-file
LCOV and does not depend on these packages.

Tested on 2026-11-01 with CircleCI CLI core 1.13.0, isolated testsuite extension
1.0.68450-8be2f5e, pytest 9.1.1, coverage.py 7.16.2,
pytest-cov 7.1.0, and pytest-circleci-coverage 0.3.883.

Copy this directory to an empty temporary directory so the repository's own
`.circleci/test-suites.yml` cannot take precedence, then run from the copy:

```bash
probe=$(mktemp -d)
cp -R docs/runbooks/fixtures/circleci-single-pass-repro/. "$probe/"
cd "$probe"
circleci testsuite doctor "parser reproduction"
```

The discovery, sampled run, and analysis checks passed with the pinned extension.
For Fleet, the separate doctor check failed in its sampled run due to a sandbox
fixture not being loaded. The bounded attempt therefore selected LCOV fallback.
