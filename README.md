# krz_prnt-feed
Supplier feeds for the kruza.lv XML import, built by GitHub Actions and published on GitHub Pages.

## F9 Baltic (`f9/f9_feed.py`, workflow `F9 Baltic XML feed`)

Secrets (Settings → Secrets and variables → Actions → Secrets): `F9_USER`, `F9_PASS`, `F9_CLIENT_ID`, `F9_KEY`.

Variables: `PAGES_URL` (site address, no trailing slash), `F9_FEED_FILE` (hard-to-guess file name, e.g. `f9-<random>.xml`).
Optional: `F9_ONLY_EAN=1`, `F9_SKIP_CATS=WAR,SOF,...`.

Run manually with **probe** ticked first: the log shows every field the API returns (no file is published).

Output keeps the old `f9_catalog.xml` format (`products/product`: kods, ean, nosaukums, razotajs, noliktava,
cena, kategorija, serija, akcija) and adds `kategorija_serija`, `noliktava_<warehouse>`, `grp_*` and any other
fields the API sends. Products with 0 stock stay in the file so the import sets their stock to 0.

Both workflows deploy the same Pages site, so each one first downloads the other feed and publishes it again.
