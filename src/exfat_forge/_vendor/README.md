# PS5 fake-SELF helper

`make_fself.py` is an unmodified copy of
`samples/install_app/make_fself.py` from `ps5-payload-dev/sdk`, used to turn a
patched plaintext PS5 ELF back into a fake-signed SELF container.

- Upstream: https://github.com/ps5-payload-dev/sdk
- Retrieved: 2026-08-22
- SHA-256: `2894C0114371B680E305B5CBF9DCC643ABF8A48B0E70D29B01D2B81AF9C66E94`
- License: GPL-3.0-or-later; see `PS5_SDK_LICENSE`.

The SELF extraction and SDK-patching code in `exfat_forge.backport` is an
independent implementation and is not copied from the license-unclear embedded
Backport scripts in the reference application.
