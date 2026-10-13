"""
scripts/verify_mobile_pwa.py
Verification test for Focus Engine Mobile PWA enhancements.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def test_mobile_pwa():
    html = Path("static/index.html").read_text(encoding="utf-8")
    sw = Path("static/sw.js").read_text(encoding="utf-8")
    manifest = Path("static/manifest.json").read_text(encoding="utf-8")

    # 1. Manifest
    assert "shortcuts" in manifest, "Shortcuts missing from manifest.json"
    assert "/#hud" in manifest, "HUD shortcut missing from manifest.json"
    assert "/#park" in manifest, "Park shortcut missing from manifest.json"
    assert "standalone" in manifest, "Standalone display missing from manifest.json"
    print("✓ Manifest verified with PWA standalone shortcuts")

    # 2. Service Worker Cache
    assert "focus-engine-v5.0-mobile-hud" in sw, "Cache version not bumped in sw.js"
    print("✓ Service Worker cache bumped to v5.0-mobile-hud")

    # 3. HTML Navigation Dock
    assert 'id="mobile-bottom-dock"' in html, "mobile-bottom-dock missing"
    assert 'id="dock-btn-hud"' in html, "dock-btn-hud missing"
    assert 'id="dock-btn-tasks"' in html, "dock-btn-tasks missing"
    assert 'id="dock-btn-fab"' in html, "dock-btn-fab missing"
    assert 'id="dock-btn-analytics"' in html, "dock-btn-analytics missing"
    assert 'id="dock-btn-settings"' in html, "dock-btn-settings missing"
    print("✓ Mobile Bottom Navigation Dock verified (5 ergonomic tabs)")

    # 4. Mobile Quick Action Sheet
    assert 'id="mobile-action-sheet"' in html, "mobile-action-sheet missing"
    assert 'sheet-drag-handle' in html, "sheet-drag-handle missing"
    assert 'mobile-sound-channel-btn' in html, "mobile-sound-channel-btn missing"
    print("✓ Mobile Tactical Action Sheet verified (Tactical FAB + Audio strip)")

    # 5. Haptics and Gestures
    assert "function triggerHaptic(" in html, "triggerHaptic missing"
    assert "function mobileNavTo(" in html, "mobileNavTo missing"
    assert "function toggleMobileActionSheet(" in html, "toggleMobileActionSheet missing"
    assert "function initMobileNav(" in html, "initMobileNav missing"
    assert "function initModalTouchGestures(" in html, "initModalTouchGestures missing"
    print("✓ Haptic Feedback Engine & Touch Gestures verified")

    print("\n🎉 ALL MOBILE PWA ENHANCEMENTS VERIFIED SUCCESSFULLY!")

if __name__ == "__main__":
    test_mobile_pwa()
