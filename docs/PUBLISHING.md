# Publishing with clean Git history

The current working tree can be cleaned, but deleting a binary in a new commit does not erase it from old commits. Anyone who can clone a public repository can inspect all reachable history.

## Required checks

```powershell
.\scripts\public-release-audit.ps1
.\scripts\public-release-audit.ps1 -IncludeHistory
```

The history audit intentionally fails if any reachable commit contains DSDPlus, FMP24, FFmpeg, MediaMTX, local databases, recordings, temporary schema dumps, secrets, or other blocked artifacts.

## Safest first public release

1. Finish and commit the reviewed source in the existing private repository.
2. Export that one reviewed tree with `scripts\export-public-tree.ps1 -Destination C:\path\xscan-public`.
3. Inspect the export and run a secret scanner such as Gitleaks or GitHub secret scanning.
4. In the exported directory, run `git init`, make a new initial commit, and connect it to a **new empty public repository**.
5. Confirm the old private remote remains private. Do not force-push rewritten history over it as part of the first public release.

The export script refuses a dirty source tree and a non-empty destination. It copies only the committed tree; `.git`, ignored runtime data, and old history are not included.
