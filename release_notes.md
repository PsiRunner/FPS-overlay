## Fixed in 1.1.4
- **Overlay no longer gets stuck behind other windows** (e.g. a maximized browser). The coverage check crashed on every call and the error was silently swallowed, so the counter could never self-correct. It now walks the topmost z-order band directly and re-asserts topmost only when something really covers it.
- Window-handle handling hardened against 64-bit truncation.

## Smaller download
- **~22% smaller exe**: 37.4 MB -> 29.4 MB. Unused Qt plugins (software-OpenGL fallback, image codecs, SVG engine, touchscreen) and PyQt6 modules are stripped from the build. The ICO codec is kept so the tray/taskbar icon renders correctly.
- `FpsOverlay.spec` is now the single source of truth for build options; `build.bat` uses it.

## Download
- **FpsOverlay-v1.1.4.zip** - recommended (contains exe + license + credits)
- **FpsOverlay.exe** - direct raw exe

Single portable file, PresentMon bundled inside. No installer, no dependencies.

## Notes
- Windows 10/11 only; Administrator prompt required (frame-timing access)
- Unsigned build: SmartScreen may warn - **More info -> Run anyway**
- Exclusive-fullscreen games bypass the compositor; use borderless/windowed fullscreen

Found a bug? Open an issue!
