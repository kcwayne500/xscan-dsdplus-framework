# Contributing

Contributions are welcome. Keep the repository safe to clone publicly:

1. Do not add DSDPlus/FMP24, Fast Lane files, FFmpeg, MediaMTX, VB-CABLE, APKs, recordings, databases, private scan lists, passwords, keys, certificates, or machine-specific settings.
2. Put local paths and hostnames behind parameters, environment variables, or settings. Defaults must be local-only.
3. Run `v2\.venv\Scripts\python.exe -m pytest` and `powershell -File scripts\public-release-audit.ps1` before submitting a change.
4. Document new third-party code or runtime dependencies in `THIRD_PARTY_NOTICES.md`.
5. Do not include decrypted or encrypted public-safety content in fixtures or screenshots.
