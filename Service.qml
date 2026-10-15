import QtQuick
import Quickshell.Io

// Headless service: keeps `bin/tidy serve` running for as long as the plugin
// is enabled. The worker registers the Hyprland key bindings itself and keeps
// the AI backend warm; disabling the plugin stops it and removes the keys.
Item {
  id: root

  property var manifest: null

  readonly property string tidy: decodeURIComponent(Qt.resolvedUrl("bin/tidy").toString().replace(/^file:\/\//, ""))

  Process {
    id: worker
    command: [root.tidy, "serve"]
    running: true
    stdout: SplitParser {
      onRead: function(line) { console.log(line) }
    }
    onExited: function(exitCode) {
      console.warn("tidy: worker exited (" + exitCode + "), restarting in 3 s")
      restart.start()
    }
  }

  Timer {
    id: restart
    interval: 3000
    onTriggered: worker.running = true
  }
}
