import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool opened: false
  property bool running: false
  property bool expectedStop: false
  property bool responseAccepted: false
  property string statusText: "Choose a local operation. Nothing here applies or publishes changes."
  property string displayText: ""
  property int responseLimit: 16384

  function bounded(value, maximum) {
    var text = String(value === undefined || value === null ? "" : value)
    return text.length <= maximum ? text : text.substring(0, maximum)
  }

  function setPayloadField(control, payload, key, maximum) {
    if (payload[key] !== undefined) control.text = bounded(payload[key], maximum)
  }

  function open(payloadJson) {
    opened = true
    displayText = ""
    if (String(payloadJson || "").length > responseLimit) {
      statusText = "Panel payload exceeded the size limit."
      return
    }
    var payload = {}
    try {
      payload = JSON.parse(payloadJson || "{}") || {}
      if (typeof payload !== "object" || Array.isArray(payload)) throw new Error("object required")
    } catch (error) {
      statusText = "Panel payload was malformed; enter local paths below."
      return
    }
    setPayloadField(cacheField, payload, "cache", 4096)
    setPayloadField(environmentField, payload, "environment", 4096)
    setPayloadField(queryField, payload, "query", 4096)
    setPayloadField(draftField, payload, "draft", 4096)
    setPayloadField(configField, payload, "config", 4096)
    statusText = "Ready. Community results remain attributed claims."
    Qt.callLater(function() { if (root.opened) queryField.forceActiveFocus() })
  }

  function close() {
    opened = false
    if (bridge.running) {
      root.expectedStop = true
      bridge.running = false
    }
    timeout.stop()
    running = false
    displayText = ""
    statusText = "Closed"
  }

  function dismiss() {
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "community.knowledge")
    else close()
  }

  function requestObject() {
    if (mode.currentValue === "search") {
      var search = {cache: cacheField.text, query: queryField.text, intent: intent.currentValue}
      if (environmentField.text !== "") search.environment = environmentField.text
      return search
    }
    if (mode.currentValue === "status") return {cache: cacheField.text}
    return {
      draft: draftField.text,
      config: configField.text,
      destination: destination.currentValue,
      title: titleField.text,
      body: bodyField.text,
      attribution: attributionField.text
    }
  }

  function runRequest() {
    if (bridge.running) return
    var encoded = JSON.stringify(requestObject())
    if (encoded.length > responseLimit) {
      statusText = "Request exceeded the companion size limit."
      return
    }
    expectedStop = false
    responseAccepted = false
    running = true
    displayText = ""
    statusText = "Running bounded local " + mode.currentValue + "..."
    bridge.command = ["omarchy-knowledge-panel", mode.currentValue, "--request", encoded]
    bridge.running = true
    timeout.restart()
  }

  function acceptResponse(raw) {
    var response = String(raw || "")
    if (response.length > responseLimit) {
      statusText = "Toolkit response exceeded the panel size limit."
      displayText = ""
      return
    }
    try {
      var parsed = JSON.parse(response)
      if (typeof parsed !== "object" || typeof parsed.ok !== "boolean"
          || typeof parsed.status !== "string" || typeof parsed.display !== "string")
        throw new Error("shape")
      responseAccepted = true
      statusText = bounded(parsed.status, 600)
      displayText = bounded(parsed.display, 12000)
    } catch (error) {
      statusText = "Toolkit returned malformed panel data."
      displayText = ""
    }
  }

  Timer {
    id: timeout
    interval: 10000
    onTriggered: {
      if (!bridge.running) return
      root.expectedStop = true
      bridge.running = false
      root.running = false
      root.statusText = "Toolkit request exceeded the panel time limit."
    }
  }

  Process {
    id: bridge
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (!root.expectedStop) root.acceptResponse(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (!root.expectedStop && !root.responseAccepted && String(text || "").trim() !== "")
          root.statusText = "The local companion bridge could not complete the request."
      }
    }
    onExited: function(exitCode, exitStatus) {
      timeout.stop()
      root.running = false
      if (root.expectedStop) return
      if (exitCode !== 0 && !root.responseAccepted)
        root.statusText = "Request failed. Install the toolkit, then verify local paths and cache status."
    }
  }

  PanelWindow {
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "community-knowledge"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: "#b3000000"
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }

    Rectangle {
      anchors.centerIn: parent
      width: Math.min(parent.width - 48, 900)
      height: Math.min(parent.height - 48, 760)
      radius: 12
      color: "#202124"
      border.color: "#5f6368"
      border.width: 1
      MouseArea { anchors.fill: parent; onClicked: {} }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        RowLayout {
          Layout.fillWidth: true
          Label {
            text: "Community Knowledge"
            color: "#f1f3f4"
            font.pixelSize: 22
            font.bold: true
            textFormat: Text.PlainText
          }
          Item { Layout.fillWidth: true }
          Button { text: "Close"; onClicked: root.dismiss() }
        }

        Label {
          Layout.fillWidth: true
          text: "Local-only search and draft inspection. Results are attributed community claims, not official authentication."
          color: "#bdc1c6"
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
        }

        RowLayout {
          Layout.fillWidth: true
          Label { text: "Operation"; color: "#f1f3f4"; textFormat: Text.PlainText }
          ComboBox {
            id: mode
            model: [
              { text: "Search", value: "search" },
              { text: "Cache status", value: "status" },
              { text: "Draft preview", value: "preview" }
            ]
            textRole: "text"
            valueRole: "value"
          }
          TextField {
            id: cacheField
            visible: mode.currentValue !== "preview"
            Layout.fillWidth: true
            placeholderText: "Local cache path"
            maximumLength: 4096
          }
        }

        ColumnLayout {
          visible: mode.currentValue === "search"
          Layout.fillWidth: true
          TextField {
            id: queryField
            Layout.fillWidth: true
            placeholderText: "Search terms"
            maximumLength: 4096
            onAccepted: root.runRequest()
          }
          RowLayout {
            Layout.fillWidth: true
            ComboBox {
              id: intent
              model: [
                { text: "Corrective", value: "corrective" },
                { text: "Optional", value: "optional" },
                { text: "Undetermined", value: "undetermined" },
                { text: "All", value: "all" }
              ]
              textRole: "text"
              valueRole: "value"
            }
            TextField {
              id: environmentField
              Layout.fillWidth: true
              placeholderText: "Optional local environment JSON path"
              maximumLength: 4096
            }
          }
        }

        GridLayout {
          visible: mode.currentValue === "preview"
          Layout.fillWidth: true
          columns: 2
          Label { text: "Draft"; color: "#f1f3f4"; textFormat: Text.PlainText }
          TextField { id: draftField; Layout.fillWidth: true; maximumLength: 4096 }
          Label { text: "Routes"; color: "#f1f3f4"; textFormat: Text.PlainText }
          TextField { id: configField; Layout.fillWidth: true; maximumLength: 4096 }
          Label { text: "Destination"; color: "#f1f3f4"; textFormat: Text.PlainText }
          ComboBox { id: destination; model: ["ledger", "plugin", "toolkit", "upstream"] }
          Label { text: "Title"; color: "#f1f3f4"; textFormat: Text.PlainText }
          TextField { id: titleField; Layout.fillWidth: true; maximumLength: 4096 }
          Label { text: "Body"; color: "#f1f3f4"; textFormat: Text.PlainText }
          TextField { id: bodyField; Layout.fillWidth: true; maximumLength: 4096 }
          Label { text: "Attribution"; color: "#f1f3f4"; textFormat: Text.PlainText }
          TextField { id: attributionField; Layout.fillWidth: true; maximumLength: 4096 }
        }

        RowLayout {
          Layout.fillWidth: true
          Button {
            text: root.running ? "Working..." : (mode.currentValue === "preview" ? "Preview locally" : "Run locally")
            enabled: !root.running
            onClicked: root.runRequest()
          }
          Label {
            Layout.fillWidth: true
            text: root.statusText
            color: root.statusText.indexOf("failed") >= 0 || root.statusText.indexOf("malformed") >= 0 ? "#f28b82" : "#bdc1c6"
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }
        }

        ScrollView {
          Layout.fillWidth: true
          Layout.fillHeight: true
          TextArea {
            readOnly: true
            text: root.displayText
            color: "#e8eaed"
            selectionColor: "#5f6368"
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            font.family: "monospace"
            background: Rectangle { color: "#151618"; radius: 6 }
          }
        }
      }
    }
  }
}
