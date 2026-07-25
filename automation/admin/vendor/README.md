# Vendored Alpine CSP runtime

The Policy Editor ships a reviewed local copy of Alpine's CSP-friendly browser
build so the production page has no CDN or package-registry dependency.

- Package: `@alpinejs/csp`
- Version: `3.15.12`
- Source archive: `https://registry.npmjs.org/@alpinejs/csp/-/csp-3.15.12.tgz`
- Imported file: `package/dist/cdn.min.js` as `alpine-csp.min.js`
- File SHA-256: `566167134bb2347110904e2ced6e816d2e8d837200c158f98b72372b3bb0b9a6`
- Archive SHA-256: `323a1c203133a456aca37057b663a32bd7bdb2233e395cd0d0dfb76057f7dc3b`
- Upstream repository: `https://github.com/alpinejs/alpine/tree/main/packages/csp`
- License: MIT (`https://github.com/alpinejs/alpine/blob/main/LICENSE.md`)

## Update procedure

1. Review the target release and the CSP package provenance on npm and upstream.
2. Download the exact-version npm archive; never use a floating version URL.
3. Extract `package/dist/cdn.min.js` and replace `alpine-csp.min.js`.
4. Recompute both SHA-256 values with `sha256sum` and update this file.
5. Run `python -m pytest automation/tests/test_policy_admin_ui.py -q` in the
   project Python 3.12 image, then run the browser and Compose smoke tests.
6. Confirm the page still runs with `script-src 'self'` and without relaxed CSP
   directives before committing the update.
