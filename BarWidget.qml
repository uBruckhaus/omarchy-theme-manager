import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root

  moduleName: "ubruckhaus.theme-manager"

  readonly property string togglePath: Qt.resolvedUrl("theme-toggle").toString().replace(/^file:\/\//, "")

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // U+F1FC (paint brush) — a FontAwesome-4-era glyph present in the bar's
    // Nerd Font. FA5-era icons (e.g. palette U+F53F) are missing and render as tofu.
    text: "\uf1fc"
    tooltipText: "Theme Manager"

    onPressed: function(mouseButton) {
      if (!root.bar) return
      // Detached so a click is never dropped while a previous toggle is still running.
      Quickshell.execDetached([root.togglePath])
    }
  }
}
