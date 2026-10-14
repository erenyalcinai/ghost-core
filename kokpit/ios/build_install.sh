#!/bin/bash
# Ghost Kokpit — Mac'te derle, sahibin iPhone 17'sine kur, başlat.
#   ./build_install.sh            # hepsi: xcodegen + derle + kur + başlat
#   ./build_install.sh derle      # yalnız xcodegen + derle
#   ./build_install.sh kur        # var olan .app'i kur + başlat
#   ./build_install.sh mac        # macOS hedefi: xcodegen + derle + ~/Applications'a koy + aç
# Log: build.log (xcodebuild'in tamamı), ekrana yalnız özet düşer.
# DİKKAT: UDID sahibin iPhone 17'si. "iPhone" (13 mini, 00000000-...) Ghost'un
# telefonudur, oraya KURULMAZ.
set -uo pipefail
cd "$(dirname "$0")"

UDID="${KOKPIT_UDID:-00000000-0000-0000-0000-00000000000C}"
BUNDLE="com.example.ghost.kokpit"
SCHEME="GhostKokpit"
LOG="build.log"
ADIM="${1:-hepsi}"

derle() {
  if [ ! -f Secrets.xcconfig ]; then
    echo "UYARI: Secrets.xcconfig yok — örnekten BOŞ kopya (uygulama eşleme ekranı açar)."
    cp Secrets.xcconfig.example Secrets.xcconfig
  fi
  echo "== xcodegen"
  xcodegen generate || return 1
  echo "== xcodebuild (log: $LOG)"
  xcodebuild -project "$SCHEME.xcodeproj" -scheme "$SCHEME" \
    -destination "id=$UDID" -configuration Debug -derivedDataPath build \
    -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
    build > "$LOG" 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo "DERLEME HATASI (rc=$rc). Son satırlar:"
    grep -E "error:|BUILD" "$LOG" | tail -20
    return $rc
  fi
  grep -E "BUILD SUCCEEDED|warning:" "$LOG" | sort | uniq -c | tail -10
}

kur() {
  local app
  app=$(find build/Build/Products/Debug-iphoneos -maxdepth 1 -name '*.app' 2>/dev/null | head -1)
  if [ -z "$app" ]; then echo ".app bulunamadı; önce derle." ; return 1; fi
  echo "== kur: $app -> $UDID"
  xcrun devicectl device install app --device "$UDID" "$app" || return 1
  echo "== başlat: $BUNDLE"
  xcrun devicectl device process launch --terminate-existing --device "$UDID" "$BUNDLE"
}

# macOS hedefi (2026-09-26): aynı kaynak, ayrı .app. Kurulum = ~/Applications/Ghost.app
# (App Store yok, dev imza). Açıkken kopyalanamaz; önce kapatılır.
mac() {
  [ -f Secrets.xcconfig ] || cp Secrets.xcconfig.example Secrets.xcconfig
  echo "== xcodegen"
  xcodegen generate || return 1
  echo "== xcodebuild macOS (log: build-mac.log)"
  xcodebuild -project "$SCHEME.xcodeproj" -scheme GhostKokpitMac \
    -destination 'platform=macOS,arch=arm64' -configuration Debug -derivedDataPath build \
    -allowProvisioningUpdates build > build-mac.log 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo "DERLEME HATASI (rc=$rc). Son satırlar:"
    grep -E "error:|BUILD" build-mac.log | tail -20
    return $rc
  fi
  grep -E "BUILD SUCCEEDED|warning:" build-mac.log | sort | uniq -c | tail -10
  local app="build/Build/Products/Debug/Ghost.app"
  [ -d "$app" ] || { echo ".app yok: $app"; return 1; }
  mkdir -p "$HOME/Applications"
  osascript -e 'tell application "Ghost" to quit' >/dev/null 2>&1; sleep 1
  rm -rf "$HOME/Applications/Ghost.app"
  cp -R "$app" "$HOME/Applications/Ghost.app" || return 1
  echo "== kuruldu: $HOME/Applications/Ghost.app"
  open "$HOME/Applications/Ghost.app"
}

case "$ADIM" in
  derle) derle ;;
  mac) mac ;;
  kur) kur ;;
  hepsi) derle && kur ;;
  *) echo "kullanım: $0 [derle|kur|hepsi|mac]"; exit 2 ;;
esac
