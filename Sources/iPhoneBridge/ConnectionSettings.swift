import AppKit

@MainActor
final class ConnectionSettings: NSObject, NSWindowDelegate {
    private let panel: NSPanel
    private let device = NSPopUpButton(frame: .zero, pullsDown: false)
    private let identity = NSTextField()
    private let completion: ([String]?) -> Void
    private var finished = false

    init(inventory: [String: Any], completion: @escaping ([String]?) -> Void) {
        self.completion = completion
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 520, height: 260),
                        styleMask: [.titled, .closable], backing: .buffered, defer: false)
        super.init()
        panel.title = "Connection Settings"
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        let settings = inventory["settings"] as? [String: Any] ?? [:]
        let selected = settings["udid"] as? String
        device.addItem(withTitle: "Automatic · one connected iPhone")
        for phone in inventory["devices"] as? [[String: Any]] ?? [] {
            guard let udid = phone["udid"] as? String else { continue }
            let name = phone["name"] as? String ?? "iPhone"
            let version = phone["ios"] as? String ?? "unknown iOS"
            device.addItem(withTitle: "\(name) · iOS \(version) · \(udid.suffix(6))")
            device.lastItem?.representedObject = udid
            if udid == selected { device.select(device.lastItem) }
        }
        if let selected, device.selectedItem?.representedObject as? String != selected {
            device.addItem(withTitle: "Saved iPhone · disconnected · \(selected.suffix(6))")
            device.lastItem?.representedObject = selected
            device.select(device.lastItem)
        }
        identity.stringValue = settings["identity"] as? String ?? ""
        identity.placeholderString = "Default SSH keys and agent"
        identity.setAccessibilityLabel("SSH private key path")
        let browse = NSButton(title: "Choose…", target: self, action: #selector(chooseKey))
        let keyRow = NSStackView(views: [identity, browse])
        keyRow.orientation = .horizontal
        keyRow.spacing = 8
        identity.setContentHuggingPriority(.defaultLow, for: .horizontal)
        identity.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let help = NSTextField(wrappingLabelWithString:
            "Connect an unlocked, trusted iPhone running jailbroken iOS 15.1.1. " +
            "SSH key access as mobile must already work. Choose an existing private key if needed; only its path is saved.")
        help.textColor = .secondaryLabelColor
        help.font = .systemFont(ofSize: 12)
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancel))
        cancel.keyEquivalent = "\u{1b}"
        let save = NSButton(title: "Save & Connect", target: self, action: #selector(save))
        save.keyEquivalent = "\r"
        let buttons = NSStackView(views: [cancel, save])
        buttons.orientation = .horizontal
        buttons.spacing = 8
        let stack = NSStackView(views: [NSTextField(labelWithString: "USB device"), device,
                                      NSTextField(labelWithString: "SSH key"), keyRow, help, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false
        guard let content = panel.contentView else { return }
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 22),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -22),
            device.widthAnchor.constraint(equalTo: stack.widthAnchor),
            keyRow.widthAnchor.constraint(equalTo: stack.widthAnchor),
            help.widthAnchor.constraint(equalTo: stack.widthAnchor),
            buttons.trailingAnchor.constraint(equalTo: stack.trailingAnchor)
        ])
        panel.center()
    }

    func show() { panel.makeKeyAndOrderFront(nil) }

    @objc private func chooseKey() {
        let picker = NSOpenPanel()
        picker.title = "Choose an existing SSH private key"
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = false
        picker.showsHiddenFiles = true
        picker.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".ssh")
        picker.beginSheetModal(for: panel) { [weak self] response in
            if response == .OK, let url = picker.url { self?.identity.stringValue = url.path }
        }
    }

    @objc private func save() {
        var arguments: [String] = []
        if let udid = device.selectedItem?.representedObject as? String { arguments += ["--udid", udid] }
        else { arguments.append("--clear-udid") }
        let key = identity.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        arguments += key.isEmpty ? ["--clear-identity"] : ["--identity", key]
        finish(arguments)
    }

    @objc private func cancel() { finish(nil) }

    private func finish(_ arguments: [String]?) {
        guard !finished else { return }
        finished = true
        panel.close()
        completion(arguments)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { finish(nil); return false }
}
