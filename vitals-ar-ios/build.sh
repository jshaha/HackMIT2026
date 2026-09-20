#!/bin/sh
# Builds with a clean toolchain environment: an active conda env sets LD/CC/LDFLAGS etc., which break Xcode's linker.
cd "$(dirname "$0")"
exec env -u LD -u CC -u CXX -u AR -u NM -u RANLIB -u LDFLAGS -u CFLAGS -u CPPFLAGS -u CXXFLAGS \
  xcodebuild -project VitalsAR.xcodeproj -scheme VitalsAR -derivedDataPath build "$@"
