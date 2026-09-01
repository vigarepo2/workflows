# ecourts-4.0.5 deep reverse engineering

This output is intentionally focused on application logic rather than thousands of dependency files.

## Readable code

- `readable/hermes/app.js` — Hermes React Native application logic reconstructed by droidsaw.
- `readable/hermes/hermes-dec.js` — independent hermes-dec pseudo-code fallback when supported by the bundle version.
- `readable/native-app/` — only the app's own Java/Kotlin-like native package from JADX.

## API investigation

- `reports/HERMES_STRINGS.json` — interesting strings extracted directly from Hermes bytecode.
- `reports/HERMES_XREFS.json` — functions referencing API/auth/encryption/court-related strings.
- `reports/API_AND_CRYPTO_HITS.txt` — readable source locations for likely endpoint, request, crypto and case-search logic.
- `reports/URLS.txt` and `reports/DOMAINS.txt` — URLs/domains found in app-specific reconstructed logic.
- `reports/DROIDSAW_AUDIT.json` — cross-layer static analysis.

## Tooling

- Primary: droidsaw (APK + DEX + Hermes v40-v100 analysis/decompilation).
- Hermes fallback: current P1sec/hermes-dec main.
- Native fallback: latest stable JADX + latest stable Apktool.

## Counts

- Reconstructed Hermes JS bytes: 54201743
- App-native readable files: 4
- App-specific URLs: 651
- App-specific domains: 512
- APK SHA-256: `9459861e27ee5a0d68a625806c96346fdd156c3dac4e396aca4124b383dcaa41`

## Note

Decompilation reconstructs readable source from bytecode; it is not the developer's original source tree. For ambiguous native methods, use the full Apktool smali artifact.
