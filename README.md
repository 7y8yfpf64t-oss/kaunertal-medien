# Kaunertal original media

This repository hosts six original Kaunertal media files for the game's external-media delivery. The workflow copies the bytes unchanged: no transcoding, image conversion, volume changes, or quality reduction.

| Pinned source and repository path | Original bytes |
| --- | ---: |
| `kaunertal-trailer-v6.mp4` | 25,672,857 |
| `kaunertal-trailer-poster.jpg` | 1,140,909 |
| `web_audio/neoncholy_1up_2_few.mp3` | 7,416,785 |
| `web_audio/neoncholy_legacy_of_crosbo.mp3` | 5,440,051 |
| `web_audio/neoncholy_sunday_glide.mp3` | 4,650,522 |
| `web_audio/neoncholy_punch_up.mp3` | 1,887,084 |
| **Total** | **46,208,208** |

Each source URL is `https://kaunertal.cc/` followed by the exact path above. `media-sync.json` pins its byte length and SHA-256. The destination repository is fixed to `7y8yfpf64t-oss/kaunertal-medien`, branch `main`. Redirects, additional paths, arbitrary source URLs, and symlink destinations are rejected.

## Automatic synchronization and publication

The **Verify and sync Kaunertal originals** workflow runs when its manifest, scripts, tests, or workflow change on `main`. It can also be started through **Actions → Verify and sync Kaunertal originals → Run workflow**. There are no workflow inputs or extra secrets to configure.

1. The pinned official checkout action checks out `main`. Offline fixture tests run first.
2. Already correct media files are verified and skipped. All needed downloads finish in a temporary directory and pass independent size and SHA-256 checks before any destination is replaced.
3. Each destination replacement is atomic on the same filesystem. A failed download or checksum leaves every prior media destination unchanged. An interrupted replacement cannot create a successful media commit: the workflow commits only after all six final files verify.
4. Only the six exact media paths are staged and committed. An unchanged inventory creates no commit. Pushes never use force. A concurrent external change can cause a normal push conflict; rerun from the latest `main`.
5. The workflow checks that Pages already uses **Deploy from a branch → main → /(root)** and the expected public URL. It requests a Pages build explicitly, polls until the exact pushed commit reports `built`, and verifies all six public URLs against the manifest again. A short bounded retry covers CDN propagation. Unsupported API operations, permission failures, mismatches, build failures, and timeouts fail the workflow visibly.

The workflow uses GitHub's automatically supplied repository-scoped token with `contents: write` for the media commit and `pages: write` for the existing Pages build. It does not create or modify Pages settings. The explicit Pages build is necessary because a commit pushed using `GITHUB_TOKEN` does not itself trigger a Pages branch build.

Public URL prefix: <https://7y8yfpf64t-oss.github.io/kaunertal-medien/>. Successful workflow completion means the matching commit was built **and all six published files passed SHA-256 and size verification**. The game must separately be configured to use those URLs.

## Future hash-verified updates

Publish the intended original bytes at the same six `kaunertal.cc` source paths first. Independently calculate their sizes and SHA-256 hashes, review the intended replacements, and update the corresponding entries in `media-sync.json` on `main`. That commit automatically starts the workflow. Do not replace expected hashes with downloaded values after a mismatch merely to make a run pass.

Files whose expected hashes have not changed are skipped if already correct in this repository. New or changed files require the pinned source URL to remain available with the intended bytes. If the game later stops serving these local source paths after externalization, keep the current correct media here; a future replacement requires restoring the approved source or deliberately reviewing a script/source-pin change first. Never add a free-form download URL or token parameter.

Changes to the set of six files, source origin, or repository require an explicit code review and matching test updates. This workflow intentionally cannot delete unrelated files or select another repository.

## Offline tests

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests use small synthetic fixtures and mocked GitHub responses. They do not download media, change remote repositories, or request a Pages build.

Official references: [Pages REST API](https://docs.github.com/en/rest/pages/pages), [Pages publishing sources and token behavior](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site), [pinned checkout v4.2.2](https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683).
