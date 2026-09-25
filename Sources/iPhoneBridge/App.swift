import AppKit

@MainActor
final class MirrorApp: NSObject, NSApplicationDelegate, NSWindowDelegate, NSToolbarDelegate, NSMenuItemValidation {
    private var window: NSWindow!
    private var mirror: MirrorView!
    private let connection = MirrorConnection()
    private lazy var input = MirrorInputDriver(connection: connection)
    private lazy var mailbox = FrameMailbox { [weak self] frame, observations, count in
        guard let self, frame.geometry == self.connection.geometry else { return }
        self.benchmark?.received(frame, observations: observations, decodedCount: count)
        self.mirror.present(frame)
    }
    private lazy var decoder = HEVCDecoder(onFrame: { [mailbox] frame in mailbox.put(frame) }, onError: { [weak self] detail in
        Task { @MainActor [weak self] in self?.recoverDecoder(detail) }
    })
    private var benchmark: MirrorBenchmark?
    private lazy var recovery = KeyframeRequester(send: { [connection] in
        try await connection.command(.requestKeyframe)
    }, completed: { [weak self] error in
        guard let self, self.connected else { return }
        self.window.subtitle = error ?? "USB"
    })
    private let attachOnly = CommandLine.arguments.contains("--attach")
    private var overlay: NSStackView!
    private let message = NSTextField(wrappingLabelWithString: "Connecting to your iPhone…")
    private var retry: NSButton!
    private var settingsButton: NSButton!
    private var connectionSettings: ConnectionSettings?
    private var command: Process?
    private var quitting = false
    private var connected = false
    private var lastLandscape: Bool?
    private var phoneSize: (width: Int, height: Int)?
    private var navigationItems: [NSToolbarItem] = []
    private var shortcutMonitor: Any?
    private static let homeItem = NSToolbarItem.Identifier("phone.home")
    private static let appsItem = NSToolbarItem.Identifier("phone.apps")
    private let helper = ProcessInfo.processInfo.environment["IPHONEBRIDGE_HELPER"].map { URL(fileURLWithPath: $0) }
        ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/bridge")
    private let data = ProcessInfo.processInfo.environment["IPHONEBRIDGE_DATA_DIR"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/iPhoneBridge", isDirectory: true)

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 350, height: 758),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "iPhone"
        window.subtitle = "Connecting…"
        window.backgroundColor = .black
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.collectionBehavior = [.fullScreenPrimary]
        let toolbar = NSToolbar(identifier: "phone.navigation")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar
        window.toolbarStyle = .unifiedCompact
        window.center()
        guard configureBenchmarkWindow() else { exit(2) }

        mirror = MirrorView(frame: .zero)
        mirror.input = input
        mirror.translatesAutoresizingMaskIntoConstraints = false
        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.black.cgColor
        content.addSubview(mirror)
        window.contentView = content
        NSLayoutConstraint.activate([
            mirror.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            mirror.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            mirror.topAnchor.constraint(equalTo: content.topAnchor),
            mirror.bottomAnchor.constraint(equalTo: content.bottomAnchor)
        ])
        configureNativeMirror()
        message.textColor = .white
        message.alignment = .center
        message.font = .systemFont(ofSize: 14)
        retry = NSButton(title: "Reconnect", target: self, action: #selector(reconnect))
        retry.bezelStyle = .rounded
        retry.isHidden = true
        settingsButton = NSButton(title: "Connection Settings…", target: self, action: #selector(showSettings))
        settingsButton.bezelStyle = .rounded
        settingsButton.isHidden = true
        overlay = NSStackView(views: [message, retry, settingsButton])
        overlay.orientation = .vertical
        overlay.spacing = 18
        overlay.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(overlay)
        NSLayoutConstraint.activate([
            overlay.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            overlay.centerYAnchor.constraint(equalTo: content.centerYAnchor),
            overlay.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -48)
        ])
        window.makeKeyAndOrderFront(nil)
        installShortcuts()
        NSApp.activate(ignoringOtherApps: true)
        connect()
    }

    private func configureBenchmarkWindow() -> Bool {
        let arguments = CommandLine.arguments
        guard arguments.contains("--benchmark") else { return true }
        if let selection = arguments.first(where: { $0.hasPrefix("--benchmark-display=") }) {
            guard let identifier = UInt32(selection.dropFirst("--benchmark-display=".count)),
                  let screen = NSScreen.screens.first(where: {
                      ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == identifier
                  }) else {
                NSLog("Requested benchmark display is unavailable: %@", selection)
                return false
            }
            window.setFrameOrigin(NSPoint(x: screen.visibleFrame.midX - window.frame.width / 2,
                                          y: screen.visibleFrame.midY - window.frame.height / 2))
        }
        if arguments.contains("--benchmark-visible") { window.level = .floating }
        return true
    }

    private func makeMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About iPhoneBridge", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit iPhoneBridge", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Home Screen", action: #selector(goHome), keyEquivalent: "1")
        viewMenu.addItem(withTitle: "App Switcher", action: #selector(showApps), keyEquivalent: "2")
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: "Reconnect", action: #selector(reconnect), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewMenu.items.last?.keyEquivalentModifierMask = [.control, .command]
        viewItem.submenu = viewMenu
        menu.addItem(viewItem)
        NSApp.mainMenu = menu
    }

    private func installShortcuts() {
        // Local monitors run before dispatch to the focused mirror.
        // This only handles our two shortcuts while this app's mirror is key.
        shortcutMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            let handled = MainActor.assumeIsolated {
                guard let self, NSApp.keyWindow === self.window,
                      event.modifierFlags.intersection([.command, .control, .option, .shift]) == .command,
                      let key = event.charactersIgnoringModifiers, key == "1" || key == "2" else { return false }
                if !event.isARepeat { self.navigate(key == "1" ? "home" : "app-switcher") }
                return true
            }
            return handled ? nil : event
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let shortcutMonitor { NSEvent.removeMonitor(shortcutMonitor) }
    }

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, Self.homeItem, Self.appsItem]
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        toolbarDefaultItemIdentifiers(toolbar)
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        guard identifier == Self.homeItem || identifier == Self.appsItem else { return nil }
        let home = identifier == Self.homeItem
        let item = NSToolbarItem(itemIdentifier: identifier)
        item.label = home ? "Home Screen" : "App Switcher"
        item.toolTip = home ? "Home Screen (⌘1)" : "App Switcher (⌘2). Drag an app card up to close it."
        item.image = NSImage(systemSymbolName: home ? "square.grid.3x3" : "rectangle.on.rectangle",
                             accessibilityDescription: item.label)
        item.target = self
        item.action = home ? #selector(goHome) : #selector(showApps)
        item.autovalidates = false
        item.isEnabled = canNavigate
        item.visibilityPriority = .high
        navigationItems.append(item)
        return item
    }

    private var canNavigate: Bool { connected && phoneSize != nil && command == nil && !quitting && mirror.inputEnabled }

    private func updateNavigationControls() {
        for item in navigationItems { item.isEnabled = canNavigate }
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if menuItem.action == #selector(goHome) || menuItem.action == #selector(showApps) { return canNavigate }
        if menuItem.action == #selector(reconnect) { return command == nil && !quitting }
        if menuItem.action == #selector(showSettings) { return command == nil && !quitting }
        return true
    }

    @objc private func goHome() { navigate("home") }
    @objc private func showApps() { navigate("app-switcher") }

    private func navigate(_ destination: String) {
        guard canNavigate else { return }
        mirror.navigate(button: destination == "home" ? 1 : 2)
        window.makeFirstResponder(mirror)
    }

    @objc private func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "iPhoneBridge",
            .applicationVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            .credits: NSAttributedString(string: "Live iPhone mirroring over USB.\nOriginal bridge code: MIT.\nPhone components derived from TrollVNC retain their GPL license.\ngithub.com/net-snix/iPhoneBridge")])
    }

    @objc private func showSettings() {
        guard command == nil, !quitting else { return }
        if let connectionSettings { connectionSettings.show(); return }
        runBridge(["devices"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            guard success, let json = detail.data(using: .utf8),
                  let inventory = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else {
                let alert = NSAlert()
                alert.messageText = "Couldn’t read connection settings"
                alert.informativeText = detail
                alert.beginSheetModal(for: self.window)
                return
            }
            let settings = ConnectionSettings(inventory: inventory) { [weak self] arguments in
                guard let self else { return }
                self.connectionSettings = nil
                if let arguments { self.applySettings(arguments) }
            }
            self.connectionSettings = settings
            settings.show()
        }
    }

    private func applySettings(_ arguments: [String]) {
        guard command == nil, !quitting else { return }
        showMessage("Updating connection…", canRetry: false)
        recovery.cancel(); connection.stop(); decoder.reset(); mirror.clear()
        runBridge(["stop"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            guard success else { self.showMessage(detail, canRetry: true); return }
            self.runBridge(["configure"] + arguments) { [weak self] saved, error in
                guard let self, !self.quitting else { return }
                if saved { self.connect() }
                else { self.showMessage(error, canRetry: true) }
            }
        }
    }

    private func connect() {
        guard command == nil, !quitting else { return }
        showMessage("Connecting to your iPhone…", canRetry: false)
        if attachOnly { connection.start(); return }
        runBridge(["connect"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            if success {
                self.connection.start()
            } else {
                self.showMessage("Couldn’t connect.\n\n" + detail, canRetry: true)
            }
        }
    }

    @objc private func reconnect() {
        guard command == nil, !quitting else { return }
        connected = false
        showMessage("Reconnecting…", canRetry: false)
        recovery.cancel(); connection.stop(); decoder.reset(); mirror.clear()
        if attachOnly { connection.start(); return }
        runBridge(["stop"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            if success { self.connect() }
            else { self.showMessage(detail, canRetry: true) }
        }
    }

    private func showMessage(_ text: String, canRetry: Bool) {
        connected = false
        phoneSize = nil
        updateNavigationControls()
        message.stringValue = text
        retry.isHidden = !canRetry
        settingsButton.isHidden = !canRetry
        overlay.isHidden = false
        mirror.isHidden = true
        window.subtitle = canRetry ? "Disconnected" : "Connecting…"
    }

    private func runBridge(_ arguments: [String], completion: @escaping @MainActor (Bool, String) -> Void) {
        let process = Process()
        process.executableURL = helper
        process.arguments = arguments
        process.currentDirectoryURL = data
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        environment["IPHONEBRIDGE_DATA_DIR"] = data.path
        process.environment = environment
        let logURL = data.appendingPathComponent("logs/app-command.log")
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            let log = try FileHandle(forWritingTo: logURL)
            process.standardOutput = log
            process.standardError = log
            process.standardInput = FileHandle.nullDevice
            process.terminationHandler = { [weak self] finished in
                try? log.close()
                let output = (try? String(contentsOf: logURL, encoding: .utf8)) ?? "See \(logURL.path)."
                Task { @MainActor [weak self] in
                    self?.command = nil
                    self?.updateNavigationControls()
                    completion(finished.terminationStatus == 0, output.trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
            command = process
            updateNavigationControls()
            try process.run()
        } catch {
            command = nil
            updateNavigationControls()
            completion(false, error.localizedDescription)
        }
    }

    private func configureNativeMirror() {
        input.onError = { [weak self] detail in
            self?.mirror.cancelInput()
            self?.window.subtitle = detail
        }
        mirror.onDisplayError = { [weak self] detail in self?.recoverDecoder(detail) }
        mirror.onSubmitted = { [weak self] frame in self?.benchmark?.submitted(frame) }
        connection.onGeometry = { [weak self] geometry in
            guard let self else { return }
            self.mirror.geometry = geometry
            self.phoneSize = (Int(geometry.width), Int(geometry.height))
            self.resizePhone(width: Double(geometry.width), height: Double(geometry.height))
            self.updateNavigationControls()
        }
        connection.onFormat = { [weak self] format in self?.decoder.configure(format) }
        connection.onVideo = { [weak self] video in self?.decoder.submit(video) }
        connection.onState = { [weak self] connected, detail in
            guard let self, !self.quitting else { return }
            self.connected = connected
            if connected {
                self.overlay.isHidden = true; self.mirror.isHidden = false
                self.window.subtitle = "USB"
                self.window.makeFirstResponder(self.mirror)
            } else {
                self.recovery.cancel(); self.decoder.reset(); self.mailbox.reset(); self.mirror.clear()
                self.showMessage(detail, canRetry: true)
            }
            self.updateNavigationControls()
        }
        if CommandLine.arguments.contains("--benchmark") {
            benchmark = MirrorBenchmark(connection: connection, window: window, decoder: decoder,
                prepareInput: { [weak self] in await self?.input.cancelAndWait() }) { [weak self] active in
                self?.mirror.inputEnabled = !active
                self?.updateNavigationControls()
            }
            benchmark?.start()
        }
    }

    private func recoverDecoder(_ detail: String) {
        guard connected else { return }
        window.subtitle = detail
        recovery.request()
    }

    func windowDidResignKey(_ notification: Notification) { mirror.cancelInput() }
    func applicationDidResignActive(_ notification: Notification) { mirror.cancelInput() }

    private func resizePhone(width: Double, height: Double) {
        let landscape = width > height
        window.contentAspectRatio = NSSize(width: width, height: height)
        window.contentMinSize = landscape ? NSSize(width: 400, height: 180) : NSSize(width: 240, height: 480)
        guard lastLandscape != landscape, !window.styleMask.contains(.fullScreen) else { return }
        lastLandscape = landscape
        let screen = (window.screen ?? NSScreen.main)?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 900)
        let scale = min(landscape ? 850 / width : 380 / width, (screen.height - 100) / height, (screen.width - 80) / width)
        let contentSize = NSSize(width: width * scale, height: height * scale)
        let oldFrame = window.frame
        let frameSize = window.frameRect(forContentRect: NSRect(origin: .zero, size: contentSize)).size
        let origin = NSPoint(x: max(screen.minX, min(oldFrame.midX - frameSize.width / 2, screen.maxX - frameSize.width)),
                             y: max(screen.minY, min(oldFrame.maxY - frameSize.height, screen.maxY - frameSize.height)))
        window.setFrame(NSRect(origin: origin, size: frameSize), display: true, animate: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        quitting = true
        benchmark?.stop(); mirror.cancelInput(); recovery.cancel(); connection.stop(); decoder.reset()
        if attachOnly { return .terminateNow }
        updateNavigationControls()
        // Finish an in-progress start/stop before issuing the final owned cleanup.
        Task { @MainActor in
            while command != nil { try? await Task.sleep(for: .milliseconds(100)) }
            runBridge(["stop"]) { _, _ in NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
}

@main
struct Main {
    @MainActor static func main() {
        if let fixture = CommandLine.arguments.first(where: { $0.hasPrefix("--decode-fixture=") }) {
            let output = CommandLine.arguments.first(where: { $0.hasPrefix("--decode-output=") })?.dropFirst("--decode-output=".count)
            exit(HEVCFixture.verify(path: String(fixture.dropFirst("--decode-fixture=".count)), output: output.map(String.init),
                                   scanBarcode: CommandLine.arguments.contains("--decode-barcode")))
        }
        let app = NSApplication.shared
        let delegate = MirrorApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
