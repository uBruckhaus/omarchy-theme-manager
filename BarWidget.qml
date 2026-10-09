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
    // U+F0E0C (Material Design palette outline): the icon Omarchy's own menu
    // uses for Style → Theme. Nerd Fonts carry it; FontAwesome-5 code points
    // such as U+F53F are missing from the bar font and render as tofu.
    text: "\u{F0E0C}"
    tooltipText: "Theme Manager"

    onPressed: function(mouseButton) {
      if (!root.bar) return
      // Detached so a click is never dropped while a previous toggle is still running.
      Quickshell.execDetached([root.togglePath])
    }
  }
}
