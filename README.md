# Guardly downloads

This repository publishes Guardly Linux downloads and signed update metadata. Application and connector source repositories remain separate. Corresponding source and notices accompany each published binary through immutable GitHub Release assets.

No application release is currently published. The signing authority is in `authority.json`; its public key authenticates update manifests after an independently trusted bootstrap installation. Private keys and publisher state remain with the offline operator.

The manual Pages workflow downloads only catalogued release assets, verifies their SHA-256 and length, verifies the stable manifest's detached P-256 signature, and deploys the site as one artifact. Keep at most three AppImages and a total site budget below 900 MB. Source archives stay on Releases and must fit GitHub's 2 GiB per-file limit. Never commit binary archives or private evidence here.

Before adding a release, validate its installed upgrade and rollback journey under this authority, scan the public files for secrets, and record exact binary/source hashes and source provenance. Upload assets before cataloguing them. Asset replacement requires a new release identity. Dispatch the workflow after review, then verify direct HTTPS status, absence of redirects, exact bytes and signatures at the public endpoints. A successful workflow alone does not establish consumer acceptance.
